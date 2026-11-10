import asyncio
import time
from collections import defaultdict

import httpx
import pytest
from fakes import UNB, add_user, sign_in
from fastapi.testclient import TestClient

from app import autosync as autosync_module
from app.accounts import Accounts
from app.api import create_app
from app.autosync import (
    AUTO_PAGES,
    HOUR,
    ITEMS,
    MANUAL,
    MESSAGE,
    RETRY_AFTER,
    SCHEDULE,
    VISIT,
    AutoSync,
)
from app.items import ItemService
from app.orders import OrderService
from xianyu_core.errors import MtopError
from xianyu_core.guard import Guard
from xianyu_core.order import BOUGHT, SOLD

ITEM_LIST = "mtop.idle.web.xyh.item.list"
SOLD_LIST = "mtop.taobao.idle.trade.merchant.sold.get"
BOUGHT_LIST = "mtop.idle.web.trade.bought.list"


def make(database, accounts, events=None, **options) -> AutoSync:
    return AutoSync(
        database,
        accounts,
        defaultdict(asyncio.Lock),
        ItemService(database, accounts, interval=0),
        OrderService(database, accounts, interval=0),
        publish=(lambda account_id, event: events.append(event)) if events is not None else None,
        **options,
    )


def by_job(service: AutoSync) -> dict:
    return {s.job: s for s in service.statuses(UNB)}


def calls(platform, api: str) -> int:
    return [name for name, _ in platform.calls].count(api)


def test_manual_run_is_recorded_and_pushed(database, accounts, platform):
    events = []
    service = make(database, accounts, events)
    assert by_job(service)[ITEMS].last_run_at is None
    assert by_job(service)[ITEMS].interval == 3 * HOUR

    result = asyncio.run(service.run(UNB, ITEMS, MANUAL))

    assert sorted(result.added) == ["1001", "1002"]
    status = by_job(service)[ITEMS]
    assert (status.last_trigger, status.last_error) == (MANUAL, None)
    assert status.last_result == {"added": 2, "updated": 0, "gone": 0}
    assert status.last_ok_at == status.last_run_at
    # 下次定时同步从这次往后顺延一个间隔（多等 0～10%）
    assert 3 * HOUR <= status.next_run_at - status.last_run_at <= 3.3 * HOUR
    assert events == [
        {"type": "sync", "job": ITEMS, "running": True},
        {"type": "sync", "job": ITEMS, "running": False},
    ]


def test_failure_keeps_last_result_and_retries_sooner(database, accounts, platform):
    service = make(database, accounts)
    asyncio.run(service.run(UNB, ITEMS, MANUAL))
    ok_at = by_job(service)[ITEMS].last_ok_at

    platform.fail[ITEM_LIST] = "FAIL_BIZ_X::平台出错"
    with pytest.raises(MtopError):
        asyncio.run(service.run(UNB, ITEMS, SCHEDULE))

    status = by_job(service)[ITEMS]
    assert "平台出错" in status.last_error
    assert status.last_trigger == SCHEDULE
    assert status.last_ok_at == ok_at
    assert status.last_result == {"added": 2, "updated": 0, "gone": 0}  # 上次成功的结果留着
    assert status.next_run_at - status.last_run_at <= RETRY_AFTER + 1

    platform.fail.clear()
    asyncio.run(service.run(UNB, ITEMS, MANUAL))
    assert by_job(service)[ITEMS].last_error is None


def test_due_jobs_run_once_per_interval(database, accounts, platform):
    service = make(database, accounts)

    ran = asyncio.run(service.run_due())

    assert ran == [(UNB, ITEMS), (UNB, SOLD), (UNB, BOUGHT)]
    assert all(s.last_trigger == SCHEDULE for s in service.statuses(UNB))
    assert asyncio.run(service.run_due()) == []  # 都还没到下次


def test_manual_sync_pushes_back_the_schedule(database, accounts, platform):
    service = make(database, accounts)
    asyncio.run(service.run(UNB, ITEMS, MANUAL))
    assert (UNB, ITEMS) not in asyncio.run(service.run_due())


def test_auto_sync_reads_only_the_newest_pages(database, accounts, platform):
    for n in range(40):
        platform.orders[str(5004000000000 + n)] = {
            "item_id": "1001",
            "status": "交易成功",
            "price": 1000,
            "created": 1761000000000 + n,
            "bought": True,
        }
    service = make(database, accounts)

    asyncio.run(service.run(UNB, BOUGHT, SCHEDULE))
    assert calls(platform, BOUGHT_LIST) == AUTO_PAGES
    assert by_job(service)[BOUGHT].last_result["added"] == AUTO_PAGES * 10

    asyncio.run(service.run(UNB, BOUGHT, MANUAL))  # 手动的翻完
    assert calls(platform, BOUGHT_LIST) == AUTO_PAGES + 4


def test_item_sync_only_lists_never_fetches_buyer_detail(database, accounts, platform):
    """同步只翻列表：逐个调买家看的商品详情（pc.detail）会被要滑块，手动的也不调。"""
    service = make(database, accounts)
    asyncio.run(service.run(UNB, ITEMS, SCHEDULE))
    asyncio.run(service.run(UNB, ITEMS, MANUAL))
    assert {name for name, _ in platform.calls} == {"mtop.idle.web.xyh.item.list"}


def test_configure_switches_and_intervals(database, accounts, platform):
    events = []
    service = make(database, accounts, events)
    asyncio.run(service.run(UNB, SOLD, MANUAL))
    last_run = by_job(service)[SOLD].last_run_at

    status = service.configure(UNB, SOLD, interval=6 * HOUR)
    assert status.interval == 6 * HOUR
    assert status.next_run_at == last_run + 6 * HOUR
    assert events[-1] == {"type": "sync", "job": SOLD, "running": False}

    service.configure(UNB, ITEMS, enabled=False)
    assert (UNB, ITEMS) not in asyncio.run(service.run_due())
    assert by_job(service)[ITEMS].enabled is False
    assert by_job(service)[SOLD].interval == 6 * HOUR  # 只改了一种

    with pytest.raises(ValueError, match="间隔"):
        service.configure(UNB, ITEMS, interval=60)
    with pytest.raises(ValueError, match="不认识"):
        service.configure(UNB, "drafts", enabled=False)


def test_nothing_runs_while_account_is_blocked(database, tmp_path, platform, accounts):
    guard = Guard()
    accounts = Accounts(accounts.root, transport=httpx.MockTransport(platform), guard=guard)
    service = make(database, accounts)
    guard.trip(UNB, "RGV587_ERROR")

    assert "风控" in service.paused(UNB)
    assert asyncio.run(service.run_due()) == []
    assert platform.calls == []


def test_nothing_runs_after_login_expired(database, accounts, platform):
    platform.session_expired = True
    platform.renew_ok = False
    asyncio.run(accounts.check(UNB))
    service = make(database, accounts)

    assert "登录失效" in service.paused(UNB)
    platform.calls.clear()
    assert asyncio.run(service.run_due()) == []
    assert platform.calls == []


def test_risk_control_stops_the_rest_of_the_round(database, platform, accounts):
    accounts = Accounts(accounts.root, transport=httpx.MockTransport(platform), guard=Guard())
    service = make(database, accounts)
    platform.fail[ITEM_LIST] = "RGV587_ERROR::SM::哎哟喂"

    assert asyncio.run(service.run_due()) == [(UNB, ITEMS)]
    assert calls(platform, SOLD_LIST) == calls(platform, BOUGHT_LIST) == 0
    assert service.paused(UNB)


def test_visit_syncs_stale_jobs_in_background(database, accounts, platform):
    async def go():
        events = []
        service = make(database, accounts, events, tick=3600)
        for job in (ITEMS, SOLD, BOUGHT):
            await service.run(UNB, job, MANUAL)
        service.start()
        await asyncio.sleep(0)  # 让定时任务跑完第一轮（都没到期）
        platform.calls.clear()

        service.visit(UNB, [ITEMS])  # 刚同步过，不跑
        assert not by_job(service)[ITEMS].running

        service._stale_after = 0
        service.visit(UNB, [ITEMS])
        service.visit(UNB, [ITEMS])  # 已经在跑了，不再起一个
        assert by_job(service)[ITEMS].running
        while by_job(service)[ITEMS].running:
            await asyncio.sleep(0.01)
        await service.aclose()
        return service

    service = asyncio.run(go())
    assert calls(platform, ITEM_LIST) == 1
    assert calls(platform, SOLD_LIST) == 0  # 只跑页面要的
    assert by_job(service)[ITEMS].last_trigger == VISIT


def test_visit_does_nothing_unless_auto_sync_is_on(database, accounts, platform):
    async def go():
        service = make(database, accounts, stale_after=0)
        service.visit(UNB, [ITEMS])
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert platform.calls == []


def test_later_runs_once_after_a_burst_of_triggers(database, accounts, platform):
    async def go():
        service = make(database, accounts, tick=3600, trigger_delay=0.05)
        for job in (ITEMS, SOLD, BOUGHT):
            await service.run(UNB, job, MANUAL)
        service.start()
        await asyncio.sleep(0)
        platform.calls.clear()

        for _ in range(3):
            service.later(UNB, ITEMS)
        await asyncio.sleep(0.02)
        assert platform.calls == []  # 还在等
        deadline = time.monotonic() + 3
        while by_job(service)[ITEMS].last_trigger != MESSAGE:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        await service.aclose()

    asyncio.run(go())
    assert calls(platform, ITEM_LIST) == 1


def test_later_skips_disabled_jobs(database, accounts, platform):
    async def go():
        service = make(database, accounts, tick=3600, trigger_delay=0)
        service.configure(UNB, ITEMS, enabled=False)
        for job in (SOLD, BOUGHT):
            await service.run(UNB, job, MANUAL)
        service.start()
        await asyncio.sleep(0)
        platform.calls.clear()
        service.later(UNB, ITEMS)
        await asyncio.sleep(0.05)
        await service.aclose()

    asyncio.run(go())
    assert platform.calls == []


@pytest.fixture
def client(tmp_path, accounts, platform, database_url):
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        yield client


URL = f"/api/accounts/{UNB}/autosync"


def test_api_status_and_settings(client):
    body = client.get(URL).json()
    assert (body["active"], body["paused"]) == (False, None)
    assert body["intervals"] == [3600, 10800, 21600, 43200, 86400]
    assert [j["job"] for j in body["jobs"]] == [ITEMS, SOLD, BOUGHT]
    assert all(j["enabled"] and j["last_run_at"] is None for j in body["jobs"])

    body = client.put(f"{URL}/{SOLD}", json={"interval": 21600}).json()
    assert {j["job"]: j["interval"] for j in body["jobs"]}[SOLD] == 21600
    body = client.put(f"{URL}/{ITEMS}", json={"enabled": False}).json()
    assert {j["job"]: j["enabled"] for j in body["jobs"]} == {
        ITEMS: False,
        SOLD: True,
        BOUGHT: True,
    }
    assert client.put(f"{URL}/{SOLD}", json={"interval": 60}).status_code == 400
    assert client.put(f"{URL}/drafts", json={"enabled": True}).status_code == 400
    assert client.post(f"{URL}/visit", json={"jobs": ["drafts"]}).status_code == 400
    assert client.get("/api/accounts/999/autosync").status_code == 404


def test_api_manual_sync_is_recorded(client):
    client.post(f"/api/accounts/{UNB}/items/sync")
    client.post(f"/api/accounts/{UNB}/orders/sync", json={"side": "bought"})
    jobs = {j["job"]: j for j in client.get(URL).json()["jobs"]}
    assert jobs[ITEMS]["last_trigger"] == MANUAL
    assert jobs[ITEMS]["last_result"] == {"added": 2, "updated": 0, "gone": 0}
    assert jobs[BOUGHT]["last_trigger"] == MANUAL
    assert jobs[SOLD]["last_run_at"] is None


def test_api_visit_without_auto_sync_only_returns_status(client, platform):
    body = client.post(f"{URL}/visit", json={"jobs": [ITEMS]}).json()
    assert not any(j["running"] for j in body["jobs"])
    assert platform.calls == []


def test_serve_syncs_in_background(accounts, platform, database_url, monkeypatch):
    monkeypatch.setattr(autosync_module, "JITTER", 0)
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
        auto_sync=True,
        auto_sync_options={"tick": 3600},
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        assert client.get(URL).json()["active"] is True
        deadline = time.monotonic() + 5
        while True:
            jobs = client.get(URL).json()["jobs"]
            if all(j["last_run_at"] for j in jobs):
                break
            assert time.monotonic() < deadline, jobs
            time.sleep(0.01)
        items = client.get(f"/api/accounts/{UNB}/items").json()["items"]
    # 启动时第一轮就把从没同步过的都同步了
    assert sorted(i["item_id"] for i in items) == ["1001", "1002"]
    assert {j["last_trigger"] for j in jobs} == {SCHEDULE}
