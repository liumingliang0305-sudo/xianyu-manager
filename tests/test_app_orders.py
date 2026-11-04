import asyncio
import json
from collections import defaultdict

import pytest
from fakes import UNB

from app.items import ItemService, PriceGuardError
from app.orders import OrderFeed, OrderService, parse_order_id
from xianyu_core.errors import MtopError, RiskControlError
from xianyu_core.order import DONE, TO_SHIP, UNPAID


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def conn(database):
    return database


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
    assert (orders[0].side, orders[0].peer_nick) == ("sold", "买家甲")
    assert [o.order_id for o in service.list(UNB, status=DONE)] == ["5001"]
    raw = json.loads(
        conn.execute("SELECT raw FROM orders WHERE order_id = '5001'").fetchone()["raw"]
    )
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
    assert detail.peer_nick == "买家甲"
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
    assert (record.side, record.title, record.peer_id, record.peer_nick) == (
        "sold",
        "洗衣机",
        "7",
        "买家甲",
    )
    assert record.created_at == 1761900000

    platform.orders[order_id]["status"] = "已发货"
    record, added = run(service.add(UNB, order_id))
    assert added is False
    assert record.status == "shipped"
    assert [o.order_id for o in service.list(UNB)] == [order_id]


def test_add_bought_order_and_reject_missing(service, platform):
    order_id = add_long_order(platform, bought=True)
    record, added = run(service.add(UNB, order_id))
    assert added is True
    assert (record.side, record.peer_nick) == ("bought", "卖家乙")
    with pytest.raises(MtopError):
        run(service.add(UNB, "5009999999999"))
    assert [o.order_id for o in service.list(UNB)] == [order_id]


def details(platform) -> list[str]:
    return [d["tid"] for api, d in platform.calls if api.endswith("order.detail")]


def test_track_adds_refreshes_and_skips_finished(service, platform):
    order_id = add_long_order(platform, status="等待买家付款")
    assert run(service.track(UNB, order_id)) == "added"
    assert service.get(UNB, order_id).status == UNPAID
    platform.orders[order_id]["status"] = "等待卖家发货"
    assert run(service.track(UNB, order_id)) == "updated"
    assert service.get(UNB, order_id).status == TO_SHIP

    platform.orders[order_id]["status"] = "交易成功"
    run(service.track(UNB, order_id))
    platform.calls.clear()
    assert run(service.track(UNB, order_id)) == "skipped"  # 已经结束的不再请求
    assert platform.calls == []


def test_track_keeps_bought_orders(service, platform):
    order_id = add_long_order(platform, bought=True)
    assert run(service.track(UNB, order_id)) == "added"
    [record] = service.list(UNB, side="bought")
    assert (record.order_id, record.peer_id, record.peer_nick) == (order_id, "7", "卖家乙")
    assert service.list(UNB, side="sold") == []


def test_track_many_goes_on_after_one_order_fails(service, platform):
    good = add_long_order(platform, "5003000000001")
    bought = add_long_order(platform, "5003000000002", bought=True)
    missing = "5009999999999"
    results = run(service.track_many(UNB, [good, missing, bought, good]))
    assert results == {"added": [good, bought], "failed": [missing], "updated": [good]}


def test_track_many_stops_on_risk_control(service, platform):
    order_id = add_long_order(platform)
    platform.fail["mtop.idle.web.trade.order.detail"] = "RGV587_ERROR::SM::哎哟喂,被挤爆啦"
    with pytest.raises(RiskControlError):
        run(service.track_many(UNB, [order_id, "5003000000002"]))
    assert details(platform) == [order_id]


def test_feed_records_orders_from_trade_messages(service, platform):
    sold = add_long_order(platform, "5003000000001")
    bought = add_long_order(platform, "5003000000002", bought=True)
    changes = []

    async def go():
        feed = OrderFeed(service, defaultdict(asyncio.Lock), interval=0, on_change=changes.append)
        feed.notify(UNB, sold, "seller")
        feed.notify(UNB, sold, "seller")  # 排着队的同一笔只拉一次
        feed.notify(UNB, bought, "buyer")  # 买到的也记
        await feed.join()
        feed.notify(UNB, "5009999999999", "seller")  # 平台报错的跳过，不影响别的
        feed.notify(UNB, sold, "seller")  # 下一张卡片来了再刷新
        await feed.join()
        await feed.aclose()

    run(go())
    assert details(platform) == [sold, bought, "5009999999999", sold]
    assert changes == [UNB, UNB, UNB]
    assert [o.order_id for o in service.list(UNB, side="sold")] == [sold]
    assert [o.order_id for o in service.list(UNB, side="bought")] == [bought]


def test_feed_reports_new_orders_and_status_changes(service, platform):
    order_id = add_long_order(platform, "5003000000001")
    seen = []

    async def go():
        feed = OrderFeed(
            service,
            defaultdict(asyncio.Lock),
            interval=0,
            on_status=lambda account_id, order: seen.append((account_id, order.status)),
        )
        feed.notify(UNB, order_id, "seller")  # 新记下
        await feed.join()
        feed.notify(UNB, order_id, "seller")  # 状态没变
        await feed.join()
        platform.orders[order_id]["status"] = "已发货"
        feed.notify(UNB, order_id, "seller")  # 状态变了
        await feed.join()
        await feed.aclose()

    run(go())
    assert seen == [(UNB, TO_SHIP), (UNB, "shipped")]


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


def test_sync_bought_walks_the_list(service, platform):
    for n in range(12):
        add_long_order(platform, str(5004000000000 + n), bought=True, created=1761000000000 + n)
    result = run(service.sync(UNB, "bought"))
    assert len(result.added) == 12
    bought = service.list(UNB, side="bought")
    assert bought[0].order_id == "5004000000011"  # 新的在前
    assert (bought[0].status, bought[0].price_cent, bought[0].peer_nick) == (
        TO_SHIP,
        35800,
        "卖家乙",
    )
    assert [o.order_id for o in service.list(UNB, side="sold")] == []
    pages = [d["pageNumber"] for api, d in platform.calls if api.endswith("bought.list")]
    assert pages == [1, 2]

    platform.orders["5004000000011"]["status"] = "交易成功"
    result = run(service.sync(UNB, "bought"))
    assert (result.added, result.changed) == ([], ["5004000000011"])


def test_sync_rejects_unknown_side(service):
    with pytest.raises(ValueError, match="订单类型"):
        run(service.sync(UNB, "both"))
