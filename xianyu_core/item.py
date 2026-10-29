"""商品接口：我的商品列表、详情、编辑、下架、删除。

接口名、版本和请求体照抄网页端（xy-site 0.0.179 的个人主页、商品页和 /publish 编辑页）。
编辑时网页端先调 editDetail 拿表单初值，改完整体提交给 edit；`build_edit_payload`
就是把这段前端逻辑搬过来：只改调用方要改的字段，其余原样带回去。
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from xianyu_core.mtop import MtopClient

LIST_API = "mtop.idle.web.xyh.item.list"
DETAIL_API = "mtop.taobao.idle.pc.detail"
EDIT_DETAIL_API = "mtop.idle.pc.idleitem.editDetail"
EDIT_API = "mtop.idle.pc.idleitem.edit"
DOWNSHELF_API = "mtop.taobao.idle.item.downshelf"
DELETE_API = "com.taobao.idle.item.delete"

PAGE_SIZE = 20
# 网页端编辑页提交时固定带的来源标识
PUBLISH_SOURCE = "pcMainPublish"

SPM_PERSONAL = "a21ybx.personal.0.0"
SPM_ITEM = "a21ybx.item.0.0"
SPM_PUBLISH = "a21ybx.publish.0.0"

# 实测（2026-10-29）个人主页列表里只见过这两种；下架的商品不在列表里
STATUS_TEXT = {0: "在售", 1: "已卖出"}

_WANT = re.compile(r"(\d+)\s*人想要")


def status_text(status: int) -> str:
    return STATUS_TEXT.get(status, f"未知({status})")


def yuan_to_cent(value: str | int | float | Decimal) -> int:
    """`"358"` / `"12.5"` → 分。非法输入抛 ValueError。"""
    try:
        cents = Decimal(str(value).strip()) * 100
    except InvalidOperation as exc:
        raise ValueError(f"不是合法的金额：{value!r}") from exc
    if cents != cents.to_integral_value():
        raise ValueError(f"金额最多两位小数：{value!r}")
    return int(cents)


def cent_to_yuan(cents: int) -> str:
    """35800 → `"358"`，1050 → `"10.5"`。"""
    yuan = Decimal(cents) / 100
    text = f"{yuan:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class ItemCard:
    """个人主页列表里的一条商品。"""

    item_id: str
    title: str
    price_cent: int
    status: int
    category_id: int | None
    pic_url: str
    post_info: str  # "包邮" 之类，可能为空
    want_count: int | None  # 从"7人想要"标签解析，没有标签时是 None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def parse(cls, card: dict[str, Any]) -> ItemCard:
        data = card.get("cardData") or {}
        return cls(
            item_id=str(data.get("id", "")),
            title=data.get("title") or "",
            price_cent=yuan_to_cent((data.get("priceInfo") or {}).get("price") or 0),
            status=int(data.get("itemStatus", -1)),
            category_id=_int_or_none(data.get("categoryId")),
            pic_url=(data.get("picInfo") or {}).get("picUrl") or "",
            post_info=(data.get("detailParams") or {}).get("postInfo") or "",
            want_count=_want_count(data),
            raw=data,
        )


@dataclass
class ItemPage:
    items: list[ItemCard]
    has_next: bool


@dataclass
class ItemDetail:
    """商品详情页（买家视角，自己的商品也能查）。"""

    item_id: str
    title: str
    desc: str
    price_cent: int
    orig_price_cent: int
    quantity: int
    status: int
    status_text: str  # 平台给的文字，比如"在线"
    browse_count: int
    want_count: int
    collect_count: int
    category_id: int | None
    image_urls: list[str]
    created_at: float | None  # 秒级时间戳
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> ItemDetail:
        item = data.get("itemDO") or {}
        created = item.get("gmtCreate")
        return cls(
            item_id=str(item.get("itemId", "")),
            title=item.get("title") or "",
            desc=item.get("desc") or "",
            price_cent=yuan_to_cent(item.get("soldPrice") or 0),
            orig_price_cent=yuan_to_cent(item.get("originalPrice") or 0),
            quantity=int(item.get("quantity") or 0),
            status=int(item.get("itemStatus", -1)),
            status_text=item.get("itemStatusStr") or "",
            browse_count=int(item.get("browseCnt") or 0),
            want_count=int(item.get("wantCnt") or 0),
            collect_count=int(item.get("collectCnt") or 0),
            category_id=_int_or_none(item.get("categoryId")),
            image_urls=[img["url"] for img in item.get("imageInfos") or [] if img.get("url")],
            created_at=created / 1000 if isinstance(created, int | float) else None,
            raw=data,
        )


class ItemApi:
    """一个账号的商品接口。不管限流，调用方自己控制节奏。

    async with MtopClient(credential) as mtop:
        items = ItemApi(mtop)
        page = await items.list_page(credential.unb)
    """

    def __init__(self, mtop: MtopClient):
        self._mtop = mtop

    async def list_page(self, user_id: str, page: int = 1) -> ItemPage:
        """个人主页的商品列表，在售和已卖出都在里面，每页 20 条。"""
        data = await self._mtop.call(
            LIST_API,
            {
                "needGroupInfo": page == 1,
                "pageNumber": page,
                "userId": user_id,
                "pageSize": PAGE_SIZE,
            },
            spm_cnt=SPM_PERSONAL,
        )
        cards = [ItemCard.parse(c) for c in data.get("cardList") or [] if c.get("cardData")]
        return ItemPage(items=cards, has_next=bool(data.get("nextPage")))

    async def iter_items(
        self, user_id: str, *, interval: float = 1.0, max_pages: int = 50
    ) -> AsyncIterator[ItemCard]:
        """翻完所有页。每翻一页停 `interval` 秒。"""
        for page in range(1, max_pages + 1):
            result = await self.list_page(user_id, page)
            for item in result.items:
                yield item
            if not result.has_next or not result.items:
                return
            await asyncio.sleep(interval)

    async def detail(self, item_id: str) -> ItemDetail:
        data = await self._mtop.call(DETAIL_API, {"itemId": item_id}, spm_cnt=SPM_ITEM)
        return ItemDetail.parse(data)

    async def edit_detail(self, item_id: str) -> dict[str, Any]:
        """编辑页的表单初值（卖家视角），原样返回。价格单位是分，布尔值是字符串。"""
        return await self._mtop.call(EDIT_DETAIL_API, {"itemId": item_id}, spm_cnt=SPM_PUBLISH)

    async def edit(
        self,
        item_id: str,
        *,
        price_cent: int | None = None,
        orig_price_cent: int | None = None,
        desc: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        """改价格、原价或描述，没传的字段保持原样。返回平台回的 itemId。

        `detail` 是刚拿到的 editDetail 结果，传了就不再查一次。
        """
        if detail is None:
            detail = await self.edit_detail(item_id)
        payload = build_edit_payload(
            detail, price_cent=price_cent, orig_price_cent=orig_price_cent, desc=desc
        )
        data = await self._mtop.call(EDIT_API, payload, spm_cnt=SPM_PUBLISH)
        return str(data.get("itemId") or item_id)

    async def downshelf(self, item_id: str) -> None:
        await self._mtop.call(DOWNSHELF_API, {"itemId": item_id}, version="2.0", spm_cnt=SPM_ITEM)

    async def delete(self, item_id: str) -> None:
        await self._mtop.call(DELETE_API, {"itemId": item_id}, version="1.1", spm_cnt=SPM_ITEM)


def build_edit_payload(
    detail: dict[str, Any],
    *,
    price_cent: int | None = None,
    orig_price_cent: int | None = None,
    desc: str | None = None,
) -> dict[str, Any]:
    """editDetail 的返回 → edit 的请求体，和网页端编辑页点"发布"时提交的一致。

    网页端表单只登记了图片、描述、标签、价格、服务、邮费、地址七项，提交时再补上类目和
    几项默认值。有两处和网页端不同：
    - 数量用 editDetail 里的，网页端固定传 "1"，会把多件库存改成 1 件；
    - 标签直接用 editDetail 里的。网页端会按类目推荐重新生成一遍，`properties` 一样，
      只是字段更多。
    """
    if detail.get("itemSkuList"):
        raise ValueError("多规格商品网页端不支持编辑，请在闲鱼 App 里改")
    for name, value in (("price_cent", price_cent), ("orig_price_cent", orig_price_cent)):
        if value is not None and value < 0:
            raise ValueError(f"{name} 不能是负数")
    if price_cent is not None and price_cent == 0:
        raise ValueError("售价不能是 0")
    if desc is not None and not desc.strip():
        raise ValueError("描述不能为空")

    price = detail.get("itemPriceDTO") or {}
    text = detail.get("itemTextDTO") or {}
    post = detail.get("itemPostFeeDTO") or {}
    addr = detail.get("itemAddrDTO") or {}
    cat = detail.get("itemCatDTO") or {}
    new_desc = (text.get("desc") or "") if desc is None else desc

    return _drop_none(
        {
            "freebies": False,
            "itemTypeStr": detail.get("itemTypeStr") or "b",
            "quantity": str(detail.get("quantity") or "1"),
            "simpleItem": "true",
            "imageInfoDOList": [_image(img) for img in detail.get("imageInfoDOList") or []],
            # 网页端只有描述框，标题就是描述，平台自己截取
            "itemTextDTO": {"desc": new_desc, "title": new_desc, "titleDescSeparate": False},
            "itemLabelExtList": detail.get("itemLabelExtList") or [],
            "itemPriceDTO": {
                "origPriceInCent": _cents(
                    price.get("origPriceInCent") if orig_price_cent is None else orig_price_cent
                ),
                "priceInCent": _cents(
                    price.get("priceInCent") if price_cent is None else price_cent
                ),
            },
            "userRightsProtocols": [
                {"enable": _bool(p.get("enable")), "serviceCode": p.get("serviceCode")}
                for p in detail.get("userRightsProtocols") or []
            ],
            "itemPostFeeDTO": {
                "canFreeShipping": _bool(post.get("canFreeShipping")),
                "supportFreight": _bool(post.get("supportFreight")),
                "onlyTakeSelf": _bool(post.get("onlyTakeSelf")),
                "templateId": post.get("templateId"),
                "postPriceInCent": _cents(post.get("postPriceInCent")),
            },
            "itemAddrDTO": {
                key: addr.get(key) or ""
                for key in ("area", "city", "divisionId", "gps", "poiId", "poiName", "prov")
            },
            "itemCatDTO": {
                key: cat.get(key) or "" for key in ("catId", "catName", "channelCatId", "tbCatId")
            },
            "itemId": str(detail.get("itemId", "")),
            "uniqueCode": unique_code(),
            "sourceId": PUBLISH_SOURCE,
            "bizcode": PUBLISH_SOURCE,
            "publishScene": PUBLISH_SOURCE,
        }
    )


def unique_code() -> str:
    """网页端的写法：毫秒时间戳后面接一个 0~999 的随机数（不补零）。"""
    return f"{int(time.time() * 1000)}{random.randrange(1000)}"


def _image(img: dict[str, Any]) -> dict[str, Any]:
    extra = img.get("extraInfo") or {}
    return _drop_none(
        {
            "extraInfo": {
                "isH": extra.get("isH") or "false",
                "isT": extra.get("isT") or "false",
                "raw": extra.get("raw") or "false",
            },
            "heightSize": _int_or_none(img.get("heightSize")) or 0,
            "imgPath": img.get("imgPath"),
            "isQrCode": img.get("isQrCode") or False,
            "labels": img.get("labels") or [],
            "major": _bool(img.get("major")),
            "templateIndex": img.get("templateIndex") or "0",
            "thumbnail": img.get("thumbnail") or img.get("url"),
            "type": _int_or_none(img.get("type")) or 0,
            "url": img.get("url"),
            "widthSize": _int_or_none(img.get("widthSize")) or 0,
            "status": img.get("status") or "done",
        }
    )


def _cents(value: Any) -> str | None:
    """金额（分）统一成字符串；0 和空值不传，网页端也是这样。"""
    if value in (None, ""):
        return None
    cents = int(Decimal(str(value)))
    return str(cents) if cents else None


def _bool(value: Any) -> bool:
    return value is True or value == "true"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _want_count(data: dict[str, Any]) -> int | None:
    label_data = (data.get("itemLabelDataVO") or {}).get("labelData") or {}
    for group in label_data.values():
        for tag in (group or {}).get("tagList") or []:
            match = _WANT.search(str((tag.get("data") or {}).get("content", "")))
            if match:
                return int(match.group(1))
    return None


def _drop_none(obj: dict[str, Any]) -> dict[str, Any]:
    """去掉值为 None 的键（含一层嵌套），对应 JSON.stringify 丢掉 undefined。"""
    return {
        key: _drop_none(value) if isinstance(value, dict) else value
        for key, value in obj.items()
        if value is not None
    }
