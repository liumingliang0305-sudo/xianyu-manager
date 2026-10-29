import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core.credential import Credential
from xianyu_core.item import (
    ItemApi,
    ItemCard,
    ItemDetail,
    build_edit_payload,
    cent_to_yuan,
    yuan_to_cent,
)
from xianyu_core.mtop import MtopClient


def run(coro):
    return asyncio.run(coro)


def data_of(request: httpx.Request) -> dict:
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    return json.loads(form["data"])


def ok(data):
    return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": data})


def make_card(item_id, price="358", status=0, want="7人想要"):
    labels = {"r3": {"tagList": [{"data": {"type": "text", "content": want}}]}} if want else {}
    return {
        "cardType": 1003,
        "cardData": {
            "id": item_id,
            "title": f"商品{item_id}",
            "itemStatus": status,
            "categoryId": 50025413,
            "priceInfo": {"preText": "¥", "price": price},
            "picInfo": {"picUrl": f"http://img.example/{item_id}.jpg"},
            "detailParams": {"postInfo": "包邮"},
            "itemLabelDataVO": {"labelData": labels},
        },
    }


# 结构照 2026-10-29 实测的 editDetail 返回裁剪，数值是编的
EDIT_DETAIL = {
    "itemId": "1001",
    "itemStatus": "0",
    "itemTypeStr": "b",
    "quantity": "1",
    "simpleItem": "true",
    "imageInfoDOList": [
        {
            "major": "true",
            "templateIndex": "27923_1",
            "widthSize": "1300",
            "heightSize": "1736",
            "type": "0",
            "url": "http://img.example/a.jpg",
            "extraInfo": {"raw": "false", "isT": "false", "isH": "false"},
        },
        {
            "major": "false",
            "widthSize": "800",
            "heightSize": "800",
            "type": "0",
            "url": "http://img.example/b.heic",
        },
    ],
    "itemTextDTO": {"titleDescSeparate": "false", "title": "洗衣机", "desc": "洗衣机 九成新\n自提"},
    "itemLabelExtList": [{"propertyId": "-10000", "properties": "-10000##分类:126856141##洗衣机"}],
    "itemPriceDTO": {"origPriceInCent": "0", "priceInCent": "35800", "noBargain": "false"},
    "userRightsProtocols": [],
    "itemPostFeeDTO": {
        "canFreeShipping": "true",
        "onlyTakeSelf": "false",
        "supportFreight": "true",
        "templateId": "0",
        "postPriceInCent": "0",
    },
    "itemAddrDTO": {
        "area": "龙岗区",
        "city": "深圳",
        "prov": "广东",
        "divisionId": "440307",
        "gps": "22.6,114.1",
        "poiId": "P1",
        "poiName": "某广场",
        "aoiName": "某广场",
    },
    "itemCatDTO": {
        "catId": "50025413",
        "leafId": "872",
        "catName": "洗衣机",
        "tbCatId": "350301",
        "channelCatId": "126856141",
    },
}


def test_money_conversion():
    assert yuan_to_cent("358") == 35800
    assert yuan_to_cent("12.5") == 1250
    assert yuan_to_cent(" 0.01 ") == 1
    assert cent_to_yuan(35800) == "358"
    assert cent_to_yuan(1250) == "12.5"
    assert cent_to_yuan(1) == "0.01"
    with pytest.raises(ValueError):
        yuan_to_cent("12.345")
    with pytest.raises(ValueError):
        yuan_to_cent("abc")


def test_card_parse_reads_price_status_and_want_count():
    card = ItemCard.parse(make_card("1001", price="1410.5", status=1, want="12人想要"))
    assert (card.item_id, card.price_cent, card.status) == ("1001", 141050, 1)
    assert card.want_count == 12
    assert card.post_info == "包邮"
    assert ItemCard.parse(make_card("1002", want=None)).want_count is None


def test_iter_items_pages_until_next_page_false():
    seen = []

    def handler(request):
        body = data_of(request)
        seen.append(body)
        page = body["pageNumber"]
        cards = [make_card(f"{page}0{i}") for i in range(2)]
        return ok({"cardList": cards, "nextPage": page < 2, "totalCount": 0})

    async def go():
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            return [c.item_id async for c in ItemApi(mtop).iter_items("42", interval=0)]

    assert run(go()) == ["100", "101", "200", "201"]
    assert [b["pageNumber"] for b in seen] == [1, 2]
    assert seen[0] == {"needGroupInfo": True, "pageNumber": 1, "userId": "42", "pageSize": 20}


def test_detail_parse():
    detail = ItemDetail.parse(
        {
            "itemDO": {
                "itemId": 1001,
                "title": "洗衣机",
                "desc": "九成新",
                "soldPrice": "358",
                "originalPrice": "0",
                "quantity": 2,
                "itemStatus": 0,
                "itemStatusStr": "在线",
                "browseCnt": 102,
                "wantCnt": 7,
                "collectCnt": 3,
                "categoryId": 50025413,
                "imageInfos": [{"url": "http://img.example/a.jpg"}],
                "gmtCreate": 1773752155000,
            }
        }
    )
    assert detail.item_id == "1001"
    assert (detail.price_cent, detail.orig_price_cent, detail.quantity) == (35800, 0, 2)
    assert (detail.browse_count, detail.want_count, detail.collect_count) == (102, 7, 3)
    assert detail.created_at == 1773752155
    assert detail.image_urls == ["http://img.example/a.jpg"]


def test_edit_payload_matches_web_form():
    """2026-10-29 用网页编辑页的表单值逐字段比对过（标签除外，见 build_edit_payload）。"""
    payload = build_edit_payload(EDIT_DETAIL)
    unique = payload.pop("uniqueCode")
    assert unique.isdigit() and len(unique) >= 13
    assert payload == {
        "freebies": False,
        "itemTypeStr": "b",
        "quantity": "1",
        "simpleItem": "true",
        "imageInfoDOList": [
            {
                "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
                "heightSize": 1736,
                "isQrCode": False,
                "labels": [],
                "major": True,
                "templateIndex": "27923_1",
                "thumbnail": "http://img.example/a.jpg",
                "type": 0,
                "url": "http://img.example/a.jpg",
                "widthSize": 1300,
                "status": "done",
            },
            {
                "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
                "heightSize": 800,
                "isQrCode": False,
                "labels": [],
                "major": False,
                "templateIndex": "0",
                "thumbnail": "http://img.example/b.heic",
                "type": 0,
                "url": "http://img.example/b.heic",
                "widthSize": 800,
                "status": "done",
            },
        ],
        "itemTextDTO": {
            "desc": "洗衣机 九成新\n自提",
            "title": "洗衣机 九成新\n自提",
            "titleDescSeparate": False,
        },
        "itemLabelExtList": EDIT_DETAIL["itemLabelExtList"],
        # 原价 0 不传，和网页端一样
        "itemPriceDTO": {"priceInCent": "35800"},
        "userRightsProtocols": [],
        "itemPostFeeDTO": {
            "canFreeShipping": True,
            "supportFreight": True,
            "onlyTakeSelf": False,
            "templateId": "0",
        },
        "itemAddrDTO": {
            "area": "龙岗区",
            "city": "深圳",
            "divisionId": "440307",
            "gps": "22.6,114.1",
            "poiId": "P1",
            "poiName": "某广场",
            "prov": "广东",
        },
        "itemCatDTO": {
            "catId": "50025413",
            "catName": "洗衣机",
            "channelCatId": "126856141",
            "tbCatId": "350301",
        },
        "itemId": "1001",
        "sourceId": "pcMainPublish",
        "bizcode": "pcMainPublish",
        "publishScene": "pcMainPublish",
    }


def test_edit_payload_changes_only_requested_fields():
    base = build_edit_payload(EDIT_DETAIL)
    changed = build_edit_payload(
        EDIT_DETAIL, price_cent=29900, orig_price_cent=59900, desc="新描述 🎉"
    )
    assert changed["itemPriceDTO"] == {"origPriceInCent": "59900", "priceInCent": "29900"}
    assert changed["itemTextDTO"]["desc"] == changed["itemTextDTO"]["title"] == "新描述 🎉"
    for key in ("itemPriceDTO", "itemTextDTO", "uniqueCode"):
        base.pop(key), changed.pop(key)
    assert changed == base


def test_edit_payload_keeps_quantity_from_detail():
    assert build_edit_payload({**EDIT_DETAIL, "quantity": "5"})["quantity"] == "5"


@pytest.mark.parametrize(
    ("detail", "kwargs"),
    [
        ({**EDIT_DETAIL, "itemSkuList": [{"priceInCent": "100"}]}, {}),
        (EDIT_DETAIL, {"price_cent": 0}),
        (EDIT_DETAIL, {"price_cent": -1}),
        (EDIT_DETAIL, {"desc": "  "}),
    ],
)
def test_edit_payload_rejects_unsupported_input(detail, kwargs):
    with pytest.raises(ValueError):
        build_edit_payload(detail, **kwargs)


def test_edit_fetches_form_then_submits_change():
    seen = []

    def handler(request):
        seen.append((request.url.path, data_of(request)))
        if "editdetail" in request.url.path:
            return ok(EDIT_DETAIL)
        return ok({"itemId": "1001"})

    async def go():
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            return await ItemApi(mtop).edit("1001", price_cent=30000)

    assert run(go()) == "1001"
    assert [path for path, _ in seen] == [
        "/h5/mtop.idle.pc.idleitem.editdetail/1.0/",
        "/h5/mtop.idle.pc.idleitem.edit/1.0/",
    ]
    assert seen[0][1] == {"itemId": "1001"}
    assert seen[1][1]["itemPriceDTO"] == {"priceInCent": "30000"}
    assert seen[1][1]["itemId"] == "1001"


def test_downshelf_and_delete_use_web_api_versions():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert data_of(request) == {"itemId": "1001"}
        return ok({})

    async def go():
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            api = ItemApi(mtop)
            await api.downshelf("1001")
            await api.delete("1001")

    run(go())
    assert paths == [
        "/h5/mtop.taobao.idle.item.downshelf/2.0/",
        "/h5/com.taobao.idle.item.delete/1.1/",
    ]
