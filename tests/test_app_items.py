import asyncio

import pytest
from fakes import UNB

from app.accounts import AccountNotFoundError, Accounts
from app.items import ItemService, PriceGuardError
from xianyu_core.credential import Credential
from xianyu_core.errors import MtopError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def service(accounts, database):
    return ItemService(database, accounts, interval=0)


def apis(platform):
    return [api for api, _ in platform.calls]


def test_sync_stores_items_and_marks_missing_ones(service, platform):
    result = run(service.sync(UNB))
    assert sorted(result.added) == ["1001", "1002"]
    items = {it.item_id: it for it in service.list(UNB)}
    assert items["1001"].price_cent == 35800
    assert items["1002"].status == 1
    assert items["1001"].browse_count is None

    platform.items["1001"]["status"] = -1
    result = run(service.sync(UNB))
    assert result.updated == ["1002"]
    assert result.gone == ["1001"]
    assert [it.item_id for it in service.list(UNB)] == ["1002"]
    assert service.get(UNB, "1001").on_platform is False


# 买家看的商品详情接口。后台调它会被闲鱼要求过滑块（自己的商品也一样），一个都不能发
BUYER_DETAIL = "mtop.taobao.idle.pc.detail"


def stats_item(item_id="1001", **over):
    """浏览器插件发来的 pc.detail itemDO（挑过字段，见 extension/goofish.js）。"""
    return {
        "itemId": int(item_id),
        "title": "洗衣机",
        "soldPrice": "358",
        "wantCnt": 5,
        "browseCnt": 100,
        "collectCnt": 2,
        "itemStatus": 0,
    } | over


def test_fetch_detail_uses_seller_form_not_buyer_detail(service, platform):
    """点开商品详情只调卖家自己的 editDetail：用户反馈过点详情就触发风控，调的是 pc.detail。"""
    run(service.sync(UNB))
    platform.items["1001"] |= {"desc": "洗衣机 九成新\n自提", "price": 34800}
    sent = len(platform.calls)

    form = run(service.fetch_detail(UNB, "1001"))
    assert apis(platform)[sent:] == ["mtop.idle.pc.idleitem.editDetail"]
    assert form.desc == "洗衣机 九成新\n自提"
    assert (form.price_cent, form.quantity) == (34800, 1)
    assert form.image_urls == ["http://img.example/1001.jpg"]
    assert service.get(UNB, "1001").price_cent == 34800  # 顺手更新了本地的价格


def test_fetch_detail_keeps_local_price_of_multi_sku_item(service, platform):
    run(service.sync(UNB))
    platform.items["1001"] |= {"sku": [{"priceInCent": "100"}], "price": 100}
    assert run(service.fetch_detail(UNB, "1001")).multi_sku is True
    assert service.get(UNB, "1001").price_cent == 35800


def test_sync_and_detail_never_call_buyer_detail(service, platform):
    run(service.sync(UNB))
    run(service.fetch_detail(UNB, "1001"))
    assert BUYER_DETAIL not in apis(platform)
    assert service.get(UNB, "1001").browse_count is None  # 浏览、收藏同步时不拉


def test_save_stats_stores_counts_and_sync_keeps_them(service, platform):
    run(service.sync(UNB))
    sent = len(platform.calls)
    item = service.save_stats(UNB, "1001", stats_item())
    assert (item.want_count, item.browse_count, item.collect_count) == (5, 100, 2)
    assert len(platform.calls) == sent  # 不请求闲鱼

    # 只带了一部分的，别的留着原来的；写成字符串的数也认
    item = service.save_stats(UNB, "1001", {"itemId": "1001", "browseCnt": "120"})
    assert (item.want_count, item.browse_count, item.collect_count) == (5, 120, 2)

    run(service.sync(UNB))  # 列表里没有浏览、收藏，存过的不能被冲掉
    item = service.get(UNB, "1001")
    assert (item.browse_count, item.collect_count) == (120, 2)


@pytest.mark.parametrize(
    "item",
    [
        stats_item("1002"),  # 别的商品的详情
        {"itemId": 1001},  # 一个数都没有
        {"itemId": 1001, "browseCnt": -1},
        {"itemId": 1001, "browseCnt": "很多"},
        {"itemId": 1001, "browseCnt": True},
        {"itemId": 1001, "browseCnt": 2**31},
        "不是对象",
    ],
)
def test_save_stats_rejects_bad_detail(service, platform, item):
    run(service.sync(UNB))
    with pytest.raises(ValueError):
        service.save_stats(UNB, "1001", item)
    assert service.get(UNB, "1001").browse_count is None


def test_save_stats_needs_a_synced_item(service):
    with pytest.raises(ValueError, match="本地没有商品"):
        service.save_stats(UNB, "1001", stats_item())


def test_change_price_updates_platform_local_db_and_log(service, platform):
    run(service.sync(UNB))
    old = run(service.change_price(UNB, "1001", 29900))
    assert old == 35800
    assert platform.items["1001"]["price"] == 29900
    assert service.get(UNB, "1001").price_cent == 29900
    [op] = service.history(UNB, "1001")
    assert (op.action, op.ok, op.detail) == ("price", True, {"from": 35800, "to": 29900})


def test_price_guard_blocks_big_change_before_editing(service, platform):
    with pytest.raises(PriceGuardError):
        run(service.change_price(UNB, "1001", 358))  # 少输了两个 0
    with pytest.raises(PriceGuardError):
        run(service.change_price(UNB, "1001", 358000))
    assert "mtop.idle.pc.idleitem.edit" not in apis(platform)
    assert platform.items["1001"]["price"] == 35800

    run(service.change_price(UNB, "1001", 358, force=True))
    assert platform.items["1001"]["price"] == 358


def test_failed_change_is_logged_and_raised(service, platform):
    platform.fail["mtop.idle.pc.idleitem.edit"] = "FAIL_BIZ_ITEM_STATUS_ERROR::宝贝状态不允许编辑"
    with pytest.raises(MtopError):
        run(service.change_price(UNB, "1001", 30000))
    [op] = service.history(UNB)
    assert op.ok is False
    assert "宝贝状态不允许编辑" in op.error


def test_downshelf_and_delete(service, platform):
    run(service.sync(UNB))
    run(service.downshelf(UNB, "1001"))
    assert service.get(UNB, "1001").on_platform is False

    run(service.delete(UNB, "1002"))
    assert "1002" not in platform.items
    assert service.get(UNB, "1002") is None
    assert [op.action for op in service.history(UNB)] == ["delete", "downshelf"]


def test_renewed_cookies_are_saved_back(service, accounts):
    run(service.sync(UNB))
    assert accounts.load(UNB).cookies["_m_h5_tk"] == "renewed_1"


def test_accounts_resolve(tmp_path):
    accounts = Accounts(tmp_path)
    with pytest.raises(AccountNotFoundError):
        accounts.resolve(None)
    Credential(cookies={"unb": "1"}).save(tmp_path / "1.json")
    assert accounts.resolve(None) == "1"
    Credential(cookies={"unb": "2"}).save(tmp_path / "2.json")
    with pytest.raises(AccountNotFoundError):
        accounts.resolve(None)
    assert accounts.resolve("2") == "2"
    with pytest.raises(AccountNotFoundError):
        accounts.resolve("3")
