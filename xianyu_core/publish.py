"""发布新商品：传图、推荐类目、发货地、发布。

接口名、版本和请求体照抄网页端发布页（goofish.com/publish，xy-site 0.0.179 的 p_publish-index.js）。
网页端的流程：
1. 打开页面先查发布限制（prepublish.check），被限制时不让发；
2. 选图时立即传到闲鱼的上传服务，表单里只记返回的图片地址；
3. 描述或图片一变就调类目推荐，返回"分类"和几个属性（品牌、成色……）的候选，平台预先选好一项；
   改了任何一个选项再推荐一次，平台按新的选择刷新别的属性；
4. 发货地默认取 poi.get 返回的上次用的地址，也可以从常用地址里选；
5. 输入描述时查违禁词；分类定下来以后查这个分类能开的服务（userRightsProtocols）；
6. 点发布：表单值转成请求体调 publish，返回新商品的 itemId。

推荐结果里有不是普通选项的卡片（比如要填 3C 证书编号）或者是图书时，网页端提示
"当前分类不支持网页端发布"，这里也一样不发。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from xianyu_core.errors import XianyuError
from xianyu_core.item import PUBLISH_SOURCE, SPM_PUBLISH, unique_code
from xianyu_core.mtop import MtopClient

PUBLISH_API = "mtop.idle.pc.idleitem.publish"
PRECHECK_API = "mtop.idle.pc.idleitem.prepublish.check"
RECOMMEND_API = "mtop.taobao.idle.kgraph.property.recommend"
POI_API = "mtop.taobao.idle.local.poi.get"
SERVICES_API = "mtop.idle.item.publish.service.cards.list"
BADWORDS_API = "mtop.taobao.idleitem.badwords.prepubcheck"

# 图片上传服务，不是 mtop 接口。网页端发布页用 appkey=fleamarket（聊天发图是 xy_chat）
UPLOAD_URL = "https://stream-upload.goofish.com/api/upload.api"
UPLOAD_PARAMS = {"floderId": "0", "appkey": "fleamarket", "_input_charset": "utf-8"}
# 限流时上传算哪个"接口"，见 guard.py
UPLOAD_API = "stream-upload"

# 推荐、服务卡片这些接口里网页端写的场景
SCENE = "mainPublish"
# 网页端拿不到浏览器定位时用的坐标（北京东城），只用来查 poi.get 的常用地址
DEFAULT_GPS = ("116.403324", "39.919353")
# 推荐结果里没选中分类时网页端带的类目
DEFAULT_CAT_ID = "50023914"
CATEGORY = "-10000"  # 分类卡片的 propertyId
NORMAL_CARD = "20401"  # 普通的单选 / 多选卡片，别的类型网页端不支持

MAX_IMAGES = 9
MAX_DESC = 1500
MAX_POST_CENT = 100000  # 一口价邮费最多 1000 元

# 邮费的四种选法 → itemPostFeeDTO 里除邮费以外的字段（网页端发布页的单选框）
POST_MODES: dict[str, dict[str, Any]] = {
    "free": {"canFreeShipping": True, "supportFreight": True},  # 包邮
    "distance": {"canFreeShipping": False, "supportFreight": True, "templateId": "-100"},  # 按距离
    "fixed": {"canFreeShipping": False, "supportFreight": True, "templateId": "0"},  # 一口价
    "none": {"canFreeShipping": False, "supportFreight": False, "templateId": "0"},  # 无需邮寄
}
POST_MODE_TEXT = {"free": "包邮", "distance": "按距离计费", "fixed": "一口价", "none": "无需邮寄"}

# 网页端描述框不让输入 emoji。这里按常见的 emoji 码段粗判
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff\U0000fe0f\U000020e3]"
)


class PublishBlockedError(XianyuError):
    """平台不让发：账号被限制发布、描述里有违禁词。"""


@dataclass
class UploadedImage:
    url: str
    width: int
    height: int


@dataclass
class Recommendation:
    """类目推荐的结果。`cards` 是平台返回的 cardList 原样，选中的值 isClicked 是 "1"。"""

    cards: list[dict[str, Any]]
    category: dict[str, Any]  # itemCatDTO


class PublishApi:
    """一个账号的发布接口。不管限流，调用方自己控制节奏。"""

    def __init__(self, mtop: MtopClient):
        self._mtop = mtop

    async def check_limit(self) -> None:
        """账号被限制发布时抛 PublishBlockedError（网页端这时弹一个扫码去 App 的提示）。"""
        data = await self._mtop.call(PRECHECK_API, {}, spm_cnt=SPM_PUBLISH)
        info = data.get("publishLimitInfo") or {}
        if data.get("limited") and info:
            text = "，".join(t for t in (info.get("title"), info.get("content")) if t)
            raise PublishBlockedError(f"账号被限制发布：{text or '原因平台没说'}")

    async def addresses(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """(上次用的地址, 常用地址)，都是 poi.get 原样返回的。"""
        lng, lat = DEFAULT_GPS
        data = await self._mtop.call(
            POI_API,
            {"longitude": lng, "latitude": lat},
            spm_cnt=SPM_PUBLISH,
            headers={"EagleEye-UserData": "spm-cnt=a21ybx"},
        )
        return data.get("selectedPoi") or None, list(data.get("commonAddresses") or [])

    async def recommend(
        self, desc: str, cards: list[dict[str, Any]] | None = None
    ) -> Recommendation:
        """按描述推荐分类和属性。

        `cards` 是上一次推荐的结果（可以改过选中项），带上时平台按里面的选择刷新。
        """
        data: dict[str, Any] = {
            "title": desc,
            "lockCpv": False,
            "multiSKU": False,
            "publishScene": SCENE,
            "scene": "newPublishChoice",
        }
        if cards:
            chosen = _clicked(_category_card(cards))
            data.update(
                currentCardList=[card.get("cardData") or {} for card in cards],
                selectedList=labels_from_cards(cards),
                catId=(chosen or {}).get("catId") or DEFAULT_CAT_ID,
                catName=(chosen or {}).get("catName") or "",
                channelCatId=(chosen or {}).get("channelCatId") or "",
            )
        data.update(description=desc, imageInfos=[], uniqueCode=unique_code())
        result = await self._mtop.call(RECOMMEND_API, data, version="2.0", spm_cnt=SPM_PUBLISH)
        new_cards = list(result.get("cardList") or [])
        return Recommendation(cards=new_cards, category=category_from(result))

    async def services(self, item_info: dict[str, Any], cards: list[dict[str, Any]]) -> list[dict]:
        """这个分类能开的服务，按平台给的默认开关转成 userRightsProtocols。"""
        data = await self._mtop.call(
            SERVICES_API,
            {
                "cpvList": _json(cards),
                "itemInfoJson": _json({**item_info, "publishScene": SCENE}),
                "param": _json(
                    {
                        "multiSkuEditingMode": "false",
                        "settingsPreferences": None,
                        "supportDefaultOpen": True,
                    }
                ),
            },
            spm_cnt=SPM_PUBLISH,
        )
        return [
            # AI 帮卖网页端一律不开
            {
                "enable": s.get("code") != "AI_SALE" and bool(s.get("defaultEnable")),
                "serviceCode": s.get("code"),
            }
            for s in data.get("services") or []
        ]

    async def check_words(self, desc: str, user_id: str) -> None:
        """描述里有违禁词时抛 PublishBlockedError。"""
        data = await self._mtop.call(
            BADWORDS_API,
            {
                "title": desc,
                "scene": "pcPublish",
                "sessionId": f"{int(time.time() * 1000)}{user_id}",
            },
            version="2.0",
            spm_cnt=SPM_PUBLISH,
        )
        if not data.get("result"):
            return
        words = [
            word
            for item in (data.get("badWordAlertInfo") or {}).get("items") or []
            for word in item.get("words") or []
        ]
        if words or data.get("forbidPublish"):
            reason = data.get("msg") or "描述里有平台不让用的词"
            raise PublishBlockedError(f"{reason}：{'、'.join(words)}" if words else reason)

    async def upload(self, content: bytes, filename: str) -> UploadedImage:
        """传一张 JPEG 到闲鱼，返回图片地址和尺寸。"""
        body = await self._mtop.upload(
            UPLOAD_URL,
            params=UPLOAD_PARAMS,
            files={"file": (filename, content, "image/jpeg")},
            name=UPLOAD_API,
        )
        obj = body.get("object") or {}
        if not body.get("success") or not obj.get("url"):
            raise XianyuError(f"图片上传失败：{body.get('message') or '平台没给图片地址'}")
        width, _, height = str(obj.get("pix") or "").partition("x")
        return UploadedImage(url=obj["url"], width=_int(width), height=_int(height))

    async def publish(self, payload: dict[str, Any]) -> str:
        """提交发布，返回新商品的 itemId。"""
        data = await self._mtop.call(PUBLISH_API, payload, spm_cnt=SPM_PUBLISH)
        item_id = str(data.get("itemId") or "")
        if not item_id:
            raise XianyuError("发布接口返回成功，但没有商品 ID")
        return item_id


# ---- 推荐结果 ----


def unsupported_cards(cards: list[dict[str, Any]]) -> list[str]:
    """网页端不支持发布的卡片名（要填证书编号之类、图书）。空列表表示能发。"""
    return [
        (card.get("cardData") or {}).get("propertyName") or "未知"
        for card in cards
        if card.get("cardType") != NORMAL_CARD or _true((card.get("cardData") or {}).get("isBook"))
    ]


def labels_from_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """推荐卡片里选中的值 → itemLabelExtList（网页端的写法）。"""
    labels = []
    for card in cards:
        for value in (card.get("cardData") or {}).get("valuesList") or []:
            if value.get("isClicked") != "1":
                continue
            is_cat = value.get("propertyId") == CATEGORY
            text = value.get("catName" if is_cat else "valueName")
            key = value.get("channelCatId" if is_cat else "valueId")
            labels.append(
                {
                    **(value.get("transportData") or {}),
                    "text": text,
                    "isUserClick": value.get("isUserClick"),
                    "properties": f"{value.get('propertyId')}##{value.get('propertyName')}:"
                    f"{key}##{text}",
                }
            )
    return labels


def category_from(result: dict[str, Any]) -> dict[str, Any]:
    """推荐结果 → itemCatDTO：分类卡片里选中的那项，没有就用平台预测的。"""
    chosen = _clicked(_category_card(result.get("cardList") or [])) or {}
    predicted = result.get("categoryPredictResult") or {}
    return {
        key: chosen.get(key) or predicted.get(key)
        for key in ("catId", "catName", "channelCatId", "leafId", "tbCatId")
    }


def choose(cards: list[dict[str, Any]], property_id: str, keys: list[str]) -> list[dict]:
    """在推荐卡片里选中一个属性的值（多选属性可以选几个），返回改过的新列表。

    值的 key：分类是 channelCatId，别的属性是 valueId。照网页端：被选中的标成用户点的，
    原来选中、这次取消的标 isUserCancel。
    """
    found = False
    result = []
    for card in cards:
        data = card.get("cardData") or {}
        if data.get("propertyId") != property_id:
            result.append(card)
            continue
        found = True
        is_cat = property_id == CATEGORY
        values = data.get("valuesList") or []
        valid = {str(v.get("channelCatId" if is_cat else "valueId")) for v in values}
        if not keys or not set(keys) <= valid:
            raise ValueError(f"「{data.get('propertyName')}」没有这个选项")
        if len(keys) > 1 and not _true(data.get("isMultiple")):
            raise ValueError(f"「{data.get('propertyName')}」只能选一个")
        new_values = []
        for value in values:
            key = str(value.get("channelCatId" if is_cat else "valueId"))
            text = value.get("catName" if is_cat else "valueName")
            clicked = "1" if key in keys else "0"
            cancel = "1" if value.get("isClicked") == "1" and clicked == "0" else None
            new_values.append(
                {
                    **value,
                    "isClicked": clicked,
                    "isUserClick": clicked,
                    "isUserCancel": cancel,
                    "transportData": {
                        **(value.get("transportData") or {}),
                        "isUserClick": clicked,
                        "isUserCancel": cancel,
                        "properties": f"{property_id}##{data.get('propertyName')}:{key}##{text}",
                    },
                }
            )
        result.append({**card, "cardData": {**data, "valuesList": new_values}})
    if not found:
        raise ValueError("没有这个属性，先重新推荐分类")
    return result


# ---- 请求体 ----


def address_from_poi(poi: dict[str, Any]) -> dict[str, Any]:
    """poi.get 里的一个地址 → itemAddrDTO（网页端的写法）。"""
    return {
        "area": poi.get("area"),
        "city": poi.get("city"),
        "divisionId": poi.get("divisionId"),
        "gps": f"{poi.get('latitude')},{poi.get('longitude')}",
        "poiId": poi.get("poiId"),
        "poiName": poi.get("poi") or f"{poi.get('city')} {poi.get('area')}",
        "prov": poi.get("prov"),
    }


def check_desc(desc: str) -> None:
    if not desc.strip():
        raise ValueError("描述不能为空")
    if len(desc) > MAX_DESC:
        raise ValueError(f"描述最多 {MAX_DESC} 个字")
    if _EMOJI.search(desc):
        raise ValueError("描述里不能有 emoji，网页端发布不让用")


def build_publish_payload(
    *,
    desc: str,
    images: list[UploadedImage],
    cards: list[dict[str, Any]],
    category: dict[str, Any],
    price_cent: int,
    orig_price_cent: int | None,
    post_mode: str,
    post_price_cent: int | None,
    take_self: bool,
    address: dict[str, Any],
    services: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """发布的请求体，和网页端发布页点"发布"时提交的一致（不带 uniqueCode 和来源标识）。

    网页端只有描述框：标题和描述填同一串，平台自己截标题。
    """
    check_desc(desc)
    if not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError(f"图片要 1 到 {MAX_IMAGES} 张")
    if price_cent <= 0:
        raise ValueError("售价要大于 0")
    if orig_price_cent is not None and orig_price_cent < 0:
        raise ValueError("原价不能是负数")
    if post_mode not in POST_MODES:
        raise ValueError(f"不认识的邮费方式：{post_mode}")
    if post_mode == "fixed" and not 0 < (post_price_cent or 0) <= MAX_POST_CENT:
        raise ValueError("一口价邮费要大于 0、不超过 1000 元")
    if not category.get("catId"):
        raise ValueError("还没有分类，先推荐分类")
    unsupported = unsupported_cards(cards)
    if unsupported:
        raise ValueError(f"这个分类网页端不支持发布（要填{'、'.join(unsupported)}），去闲鱼 App 发")

    post = {**POST_MODES[post_mode], "onlyTakeSelf": take_self}
    if post_mode == "fixed":
        post["postPriceInCent"] = str(post_price_cent)
    return _drop_none(
        {
            "freebies": False,
            "itemTypeStr": "b",
            "quantity": "1",
            "simpleItem": "true",
            "imageInfoDOList": [
                {
                    "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
                    "isQrCode": False,
                    "url": img.url,
                    "heightSize": img.height,
                    "widthSize": img.width,
                    "major": index == 0,
                    "type": 0,
                    "status": "done",
                }
                for index, img in enumerate(images)
            ],
            "itemTextDTO": {"desc": desc, "title": desc, "titleDescSeparate": False},
            "itemLabelExtList": labels_from_cards(cards),
            "itemPriceDTO": {
                "origPriceInCent": str(orig_price_cent) if orig_price_cent else None,
                "priceInCent": str(price_cent),
            },
            "userRightsProtocols": services or [],
            "itemPostFeeDTO": post,
            "itemAddrDTO": address,
            "defaultPrice": False,
            "itemCatDTO": category,
        }
    )


def submit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """发布时网页端再补上的防重码和来源标识。"""
    return {
        **payload,
        "uniqueCode": unique_code(),
        "sourceId": PUBLISH_SOURCE,
        "bizcode": PUBLISH_SOURCE,
        "publishScene": PUBLISH_SOURCE,
    }


# ---- 内部 ----


def _category_card(cards: list[dict[str, Any]]) -> dict[str, Any]:
    for card in cards:
        if (card.get("cardData") or {}).get("propertyId") == CATEGORY:
            return card["cardData"]
    return {}


def _clicked(card_data: dict[str, Any]) -> dict[str, Any] | None:
    return next((v for v in card_data.get("valuesList") or [] if v.get("isClicked") == "1"), None)


def _true(value: Any) -> bool:
    return value is True or value == "true"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _drop_none(obj: dict[str, Any]) -> dict[str, Any]:
    """去掉值为 None 的键（含一层嵌套），对应 JSON.stringify 丢掉 undefined。"""
    return {
        key: _drop_none(value) if isinstance(value, dict) else value
        for key, value in obj.items()
        if value is not None
    }
