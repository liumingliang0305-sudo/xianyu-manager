import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core.credential import Credential
from xianyu_core.mtop import MtopClient
from xianyu_core.order import (
    CLOSED,
    DONE,
    OTHER,
    REFUND,
    SHIPPED,
    TO_SHIP,
    UNPAID,
    OrderApi,
    OrderDetail,
    SoldOrder,
    money_cent,
    order_status,
    parse_time,
)


def run(coro):
    return asyncio.run(coro)


def data_of(request: httpx.Request) -> dict:
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    return json.loads(form["data"])


def ok(data):
    return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": data})


def client_with(handler):
    credential = Credential(cookies={"unb": "42", "_m_h5_tk": "tok_1"})
    return MtopClient(credential, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("text", "in_refund", "status"),
    [
        ("等待买家付款", False, UNPAID),
        ("待发货", False, TO_SHIP),
        ("买家已付款", False, TO_SHIP),
        ("等待你发货", False, TO_SHIP),
        ("已发货", False, SHIPPED),
        ("交易成功", False, DONE),
        ("退款成功", False, CLOSED),
        ("交易关闭", False, CLOSED),
        ("退款中", False, REFUND),
        ("待发货", True, REFUND),
        ("鉴定中", False, OTHER),
    ],
)
def test_order_status(text, in_refund, status):
    assert order_status(text, in_refund) == status


def test_money_and_time_parsing():
    assert money_cent("12.50") == 1250
    assert money_cent("¥1,399") == 139900
    assert money_cent(8) == 800
    assert money_cent("") is None
    assert money_cent(None) is None
    assert parse_time("1761800000000") == 1761800000
    assert parse_time(1761800000) == 1761800000
    assert parse_time("2025-10-30 12:53:20") == 1761800000
    assert parse_time("昨天") is None


def test_sold_order_parse_tolerates_missing_fields():
    order = SoldOrder.parse({"commonData": {"orderId": "5001", "orderStatus": "交易成功"}})
    assert order.order_id == "5001"
    assert order.status == DONE
    assert order.price_cent is None
    assert order.quantity == 1
    assert order.created_at is None


def test_sold_page_uses_seller_site_request():
    seen = []

    def handler(request):
        seen.append(request)
        return ok(
            {
                "module": {
                    "items": [
                        {
                            "commonData": {
                                "orderId": "5001",
                                "itemId": "1002",
                                "orderStatus": "待发货",
                                "orderCreateTime": "1761800000000",
                            },
                            "buyerInfoVO": {"buyerId": "7"},
                            "priceVO": {"totalPrice": "1400.00"},
                        },
                        {"commonData": {}},  # 没有订单号的丢掉
                    ],
                    "nextPage": "true",
                }
            }
        )

    async def go():
        async with client_with(handler) as mtop:
            return await OrderApi(mtop).sold_page(2)

    page = run(go())
    assert page.has_next is True
    [order] = page.orders
    assert (order.order_id, order.item_id, order.status) == ("5001", "1002", TO_SHIP)
    assert order.price_cent == 140000
    assert order.created_at == 1761800000

    request = seen[0]
    assert request.url.params["api"] == "mtop.taobao.idle.trade.merchant.sold.get"
    assert request.url.params["type"] == "json"
    assert request.url.params["valueType"] == "string"
    assert request.headers["idle_site_biz_code"] == "COMMONPRO"
    assert request.headers["origin"] == "https://seller.goofish.com"
    assert data_of(request) == {
        "pageNumber": 2,
        "rowsPerPage": 20,
        "orderIds": "",
        "queryCode": "ALL",
        "orderSearchParam": "{}",
    }


# 结构照 2026-10-29 实测的一笔已完成订单裁剪，数值是编的
REAL_DETAIL = {
    "itemId": 1002,
    "orderId": 5001,
    "seller": True,
    "peerUserId": 7,
    "status": 4,
    "commonInfo": {"createTime": "1761800000000", "orderDetailUrl": "fleamarket://..."},
    "utArgs": {"orderStatus": "4", "orderStatusName": "买家已确认收货，交易成功", "seller": "true"},
    "bottomBarVO": {
        "buttonList": [{"name": "查看评价", "tradeAction": "VIEW_RATE"}, {"tradeAction": "X"}]
    },
    "components": [
        {
            "render": "orderStatusVO",
            "data": {
                "orderStatusInfo": {"title": "交易成功", "descRichText": []},
                "orderStatusNodeList": [
                    {"title": "已拍下", "completed": True, "time": "2025-10-30 12:53:20"},
                    {"title": "已评价", "completed": False, "time": None},
                ],
            },
        },
        {"render": "userGuideVO", "data": {"title": "说明"}},
        {
            "render": "orderInfoVO",
            "data": {
                "itemInfo": {"title": "平板", "buyAmount": "2", "itemMainPictCdnUrl": "http://x"},
                "priceInfo": {
                    "amount": {"title": "成交价", "value": "1400.00"},
                    "billList": [
                        {"code": "ITEM_TOTAL_FEE", "title": "商品总价", "value": "1400.00"},
                        {"code": "POST_FEE", "title": "运费", "value": "0.00"},
                    ],
                    "expectBill": {"title": "预估到手", "value": "1391.60"},
                    "softwareBill": {"title": "软件服务费", "value": "-8.40"},
                },
                "orderInfoList": [
                    {"title": "订单编号", "value": "5001", "copyable": True},
                    {"title": "收货地址", "value": "某地"},
                    {"title": "买家昵称", "value": "买家甲"},
                    {"title": "下单时间", "value": "2025-10-30 12:53:20"},
                ],
            },
        },
    ],
}


def test_detail_parse_real_structure():
    d = OrderDetail.parse("5001", REAL_DETAIL)
    assert (d.item_id, d.status, d.status_title) == ("1002", DONE, "交易成功")
    assert d.status_desc == "买家已确认收货，交易成功"  # 没有说明文字时用 utArgs 里的
    assert d.steps == [
        {"title": "已拍下", "done": True, "time": 1761800000},
        {"title": "已评价", "done": False, "time": None},
    ]
    assert (d.title, d.sku, d.quantity) == ("平板", "", 2)
    assert (d.amount_title, d.amount_cent) == ("成交价", 140000)
    assert d.bills == [
        {"title": "商品总价", "cent": 140000},
        {"title": "运费", "cent": 0},
        {"title": "软件服务费", "cent": -840},
    ]
    assert (d.income_title, d.income_cent) == ("预估到手", 139160)
    assert d.fields[1] == {"title": "收货地址", "value": "某地"}
    assert d.receiver_name == ""  # 发货后没有收货地址区块
    assert (d.seller, d.buyer_id, d.buyer_nick) == (True, "7", "买家甲")
    assert d.actions == ["查看评价"]
    assert d.created_at == 1761800000


def test_detail_parse_unpaid_with_address_block():
    data = {
        "components": [
            {
                "render": "orderStatusVO",
                "data": {
                    "orderStatusInfo": {"title": "等待你发货", "descRichText": [{"text": "请尽快"}]}
                },
            },
            {"render": "addressInfoVO", "data": {"name": "张三", "address": "某地"}},
        ]
    }
    d = OrderDetail.parse("5001", data)
    assert (d.status, d.status_desc) == (TO_SHIP, "请尽快")
    assert (d.receiver_name, d.receiver_phone, d.receiver_address) == ("张三", "", "某地")
    assert d.seller is None
    assert d.buyer_id == ""


def test_price_fields_and_adjust_price():
    seen = []

    def handler(request):
        seen.append((request.url.params["api"], data_of(request)))
        if request.url.params["api"].endswith("render"):
            return ok(
                {
                    "modifyPriceRenderList": [
                        {"key": "modifyFee", "name": "商品价格", "price": "358"},
                        {"key": "newTransportFee", "name": "运费", "price": "10.00"},
                    ]
                }
            )
        return ok({})

    async def go():
        async with client_with(handler) as mtop:
            api = OrderApi(mtop)
            fields = await api.price_fields("5002")
            await api.adjust_price("5002", {"modifyFee": 29900, "newTransportFee": 0})
            return fields

    fields = run(go())
    assert [(f.key, f.name, f.price_cent) for f in fields] == [
        ("modifyFee", "商品价格", 35800),
        ("newTransportFee", "运费", 1000),
    ]
    assert seen == [
        ("mtop.taobao.idle.trade.order.modify.price.render", {"bizOrderId": "5002"}),
        (
            "mtop.taobao.idle.trade.user.adjust.price",
            {"modifyFee": "29900", "newTransportFee": "0", "orderId": "5002"},
        ),
    ]
