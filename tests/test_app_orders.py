import asyncio
import json

import pytest
from fakes import UNB

from app import db
from app.items import ItemService, PriceGuardError
from app.orders import OrderService, parse_order_id
from xianyu_core.errors import MtopError
from xianyu_core.order import DONE, TO_SHIP, UNPAID


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def conn():
    return db.connect(":memory:")


@pytest.fixture
def service(conn, accounts):
    return OrderService(conn, accounts, interval=0)


def test_sync_stores_orders_newest_first(service, platform, conn):
    result = run(service.sync(UNB))
    assert sorted(result.added) == ["5001", "5002"]

    orders = service.list(UNB)
    assert [o.order_id for o in orders] == ["5002", "5001"]
    assert orders[0].status == UNPAID
    assert orders[0].price_cent == 36800
    assert orders[0].buyer_nick == "买家甲"
    assert [o.order_id for o in service.list(UNB, status=DONE)] == ["5001"]
    raw = json.loads(conn.execute("SELECT raw FROM orders WHERE order_id = '5001'").fetchone()[0])
    assert raw["commonData"]["orderId"] == "5001"

    platform.orders["5002"]["status"] = "待发货"
    result = run(service.sync(UNB))
    assert result.added == []
    assert result.changed == ["5002"]
    assert service.get(UNB, "5002").status == TO_SHIP


def test_sync_walks_every_page(service, platform):
    for n in range(45):
        platform.orders[str(6000 + n)] = {
            "item_id": "1001",
            "status": "交易成功",
            "price": 100,
            "created": 1761000000000 + n,
        }
    result = run(service.sync(UNB))
    assert len(result.added) == 47
    pages = [d["pageNumber"] for api, d in platform.calls if api.endswith("sold.get")]
    assert pages == [1, 2, 3]


def test_title_falls_back_to_local_items(service, conn, accounts):
    run(ItemService(conn, accounts, interval=0).sync(UNB))
    run(service.sync(UNB))
    order = service.get(UNB, "5001")
    assert order.title == "平板"


def test_fetch_detail_updates_local_record(service, platform):
    run(service.sync(UNB))
    platform.orders["5001"]["status"] = "鉴定中"  # 认不出的状态不覆盖本地
    detail = run(service.fetch_detail(UNB, "5001"))
    assert detail.title == "平板"
    assert detail.buyer_nick == "买家甲"
    order = service.get(UNB, "5001")
    assert order.status == DONE
    assert order.pic_url == "http://img.example/x.jpg"


def test_fetch_detail_does_not_add_unknown_order(service):
    run(service.fetch_detail(UNB, "5002"))
    assert service.list(UNB) == []


@pytest.mark.parametrize(
    "text",
    [
        "5002000000001",
        " 5002 0000 00001 ",
        "https://www.goofish.com/order-detail?orderId=5002000000001&role=seller",
    ],
)
def test_parse_order_id(text):
    assert parse_order_id(text) == "5002000000001"


def test_parse_order_id_rejects_garbage():
    with pytest.raises(ValueError, match="没找到订单号"):
        parse_order_id("订单 123")


def add_long_order(platform, order_id="5003000000001", **extra):
    platform.orders[order_id] = {
        "item_id": "1001",
        "status": "等待卖家发货",
        "price": 35800,
        "post": 0,
        "created": 1761900000000,
        **extra,
    }
    return order_id


def test_add_order_by_number(service, platform):
    order_id = add_long_order(platform)
    record, added = run(service.add(UNB, f"订单编号 {order_id}"))
    assert added is True
    assert (record.order_id, record.status, record.price_cent) == (order_id, TO_SHIP, 35800)
    assert (record.title, record.buyer_id, record.buyer_nick) == ("洗衣机", "7", "买家甲")
    assert record.created_at == 1761900000

    platform.orders[order_id]["status"] = "已发货"
    record, added = run(service.add(UNB, order_id))
    assert added is False
    assert record.status == "shipped"
    assert [o.order_id for o in service.list(UNB)] == [order_id]


def test_add_rejects_bought_and_missing_orders(service, platform):
    order_id = add_long_order(platform, bought=True)
    with pytest.raises(ValueError, match="是你买到的"):
        run(service.add(UNB, order_id))
    with pytest.raises(MtopError):
        run(service.add(UNB, "5009999999999"))
    assert service.list(UNB) == []


def test_sync_without_list_permission_refreshes_open_orders(service, platform):
    shipping = add_long_order(platform, "5003000000001")
    finished = add_long_order(platform, "5003000000002", status="交易成功")
    gone = add_long_order(platform, "5003000000003")
    for order_id in (shipping, finished, gone):
        run(service.add(UNB, order_id))
    platform.orders[shipping]["status"] = "已发货"
    del platform.orders[gone]
    platform.fail["mtop.taobao.idle.trade.merchant.sold.get"] = "PERMISSION_EXCEPTION::无权限访问"
    platform.calls.clear()

    result = run(service.sync(UNB))
    assert result.source == "detail"
    assert result.updated == [shipping]
    assert result.changed == [shipping]
    assert result.failed == [gone]
    details = [d["tid"] for api, d in platform.calls if api.endswith("order.detail")]
    assert finished not in details  # 已经结束的订单不再请求
    assert service.get(UNB, shipping).status == "shipped"


def test_sync_other_errors_still_raise(service, platform):
    platform.fail["mtop.taobao.idle.trade.merchant.sold.get"] = "FAIL_BIZ_X::别的错误"
    with pytest.raises(MtopError):
        run(service.sync(UNB))


def test_remove_keeps_ops(service, platform):
    run(service.sync(UNB))
    run(service.change_price(UNB, "5002", {"modifyFee": 30000}))
    assert service.remove(UNB, "5002") is True
    assert service.get(UNB, "5002") is None
    assert service.remove(UNB, "5002") is False
    assert len(service.history(UNB, "5002")) == 1


def test_change_price_fills_unchanged_fields_and_logs(service, platform):
    run(service.sync(UNB))
    old, new = run(service.change_price(UNB, "5002", {"modifyFee": 29900}))
    assert (old, new) == (36800, 30900)
    assert platform.orders["5002"]["price"] == 29900
    assert platform.orders["5002"]["post"] == 1000
    assert service.get(UNB, "5002").price_cent == 30900

    [op] = service.history(UNB, "5002")
    assert op.ok and op.action == "price"
    assert (op.detail["from"], op.detail["to"]) == (36800, 30900)
    assert op.detail["fields"][1] == {
        "key": "newTransportFee",
        "name": "运费",
        "from": 1000,
        "to": 1000,
    }


def test_change_price_guard_and_force(service, platform):
    with pytest.raises(PriceGuardError):
        run(service.change_price(UNB, "5002", {"modifyFee": 100}))
    assert platform.orders["5002"]["price"] == 35800
    assert service.history(UNB) == []

    run(service.change_price(UNB, "5002", {"modifyFee": 100}, force=True))
    assert platform.orders["5002"]["price"] == 100


@pytest.mark.parametrize(
    ("order_id", "fees", "message"),
    [
        ("5001", {"modifyFee": 100}, "只有待付款"),
        ("5002", {"discount": 100}, "没有这些价格项"),
        ("5002", {"modifyFee": 0, "newTransportFee": 0}, "必须大于 0"),
    ],
)
def test_change_price_rejects_bad_input(service, platform, order_id, fees, message):
    with pytest.raises(ValueError, match=message):
        run(service.change_price(UNB, order_id, fees))
    assert not any(api.endswith("adjust.price") for api, _ in platform.calls)


def test_failed_change_is_logged(service, platform):
    platform.fail["mtop.taobao.idle.trade.user.adjust.price"] = "FAIL_BIZ_ORDER::订单状态已变化"
    with pytest.raises(MtopError):
        run(service.change_price(UNB, "5002", {"modifyFee": 30000}))
    [op] = service.history(UNB)
    assert not op.ok
    assert "订单状态已变化" in op.error
