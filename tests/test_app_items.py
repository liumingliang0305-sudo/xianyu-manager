import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app import db
from app.accounts import AccountNotFoundError, Accounts
from app.items import ItemService, PriceGuardError
from xianyu_core.credential import Credential
from xianyu_core.errors import MtopError

UNB = "42"


def run(coro):
    return asyncio.run(coro)


class FakePlatform:
    """按接口名模拟商品相关的 mtop 接口，商品状态存在 self.items 里。"""

    def __init__(self, items):
        # item_id → {"title", "price": 分, "status"}
        self.items = items
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, str] = {}  # 接口名 → 要返回的错误码

    def __call__(self, request: httpx.Request) -> httpx.Response:
        api = request.url.params["api"]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data = json.loads(form["data"])
        self.calls.append((api, data))
        if api in self.fail:
            return httpx.Response(200, json={"ret": [self.fail[api]]})
        handler = getattr(self, api.replace(".", "_"))
        return httpx.Response(
            200,
            json={"ret": ["SUCCESS::调用成功"], "data": handler(data)},
            headers={"set-cookie": "_m_h5_tk=renewed_1; Domain=.goofish.com; Path=/"},
        )

    def mtop_idle_web_xyh_item_list(self, data):
        cards = [
            {
                "cardData": {
                    "id": item_id,
                    "title": it["title"],
                    "itemStatus": it["status"],
                    "priceInfo": {"price": str(it["price"] / 100)},
                    "picInfo": {"picUrl": ""},
                }
            }
            for item_id, it in self.items.items()
            if it["status"] in (0, 1)
        ]
        return {"cardList": cards, "nextPage": False}

    def mtop_taobao_idle_pc_detail(self, data):
        it = self.items[data["itemId"]]
        return {
            "itemDO": {
                "itemId": int(data["itemId"]),
                "title": it["title"],
                "soldPrice": str(it["price"] / 100),
                "itemStatus": it["status"],
                "browseCnt": 100,
                "wantCnt": 5,
                "collectCnt": 2,
            }
        }

    def mtop_idle_pc_idleitem_editDetail(self, data):
        it = self.items[data["itemId"]]
        return {
            "itemId": data["itemId"],
            "itemTextDTO": {"desc": it["title"]},
            "itemPriceDTO": {"priceInCent": str(it["price"])},
        }

    def mtop_idle_pc_idleitem_edit(self, data):
        it = self.items[data["itemId"]]
        it["price"] = int(data["itemPriceDTO"]["priceInCent"])
        it["title"] = data["itemTextDTO"]["desc"]
        return {"itemId": data["itemId"]}

    def mtop_taobao_idle_item_downshelf(self, data):
        self.items[data["itemId"]]["status"] = -1  # 下架后不在主页列表里
        return {}

    def com_taobao_idle_item_delete(self, data):
        del self.items[data["itemId"]]
        return {}


@pytest.fixture
def platform():
    return FakePlatform(
        {
            "1001": {"title": "洗衣机", "price": 35800, "status": 0},
            "1002": {"title": "平板", "price": 140000, "status": 1},
        }
    )


@pytest.fixture
def accounts(tmp_path, platform):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1"}).save(tmp_path / f"{UNB}.json")
    return Accounts(tmp_path, transport=httpx.MockTransport(platform))


@pytest.fixture
def service(accounts):
    return ItemService(db.connect(":memory:"), accounts, interval=0)


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


def test_sync_with_stats_fetches_detail_for_online_items_only(service, platform):
    run(service.sync(UNB, with_stats=True))
    assert apis(platform).count("mtop.taobao.idle.pc.detail") == 1
    item = service.get(UNB, "1001")
    assert (item.browse_count, item.want_count, item.collect_count) == (100, 5, 2)


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


def test_change_desc_logs_before_and_after(service, platform):
    run(service.change_desc(UNB, "1001", "洗衣机 九成新"))
    assert platform.items["1001"]["title"] == "洗衣机 九成新"
    [op] = service.history(UNB)
    assert op.detail == {"from": "洗衣机", "to": "洗衣机 九成新"}


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
