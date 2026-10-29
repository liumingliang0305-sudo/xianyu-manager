import asyncio

import httpx
import pytest
from fakes import UNB

from app import cli
from app.accounts import Accounts, LoginState, make_guard
from xianyu_core.credential import Credential
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    RateLimitedError,
    RiskControlError,
    XianyuError,
)
from xianyu_core.guard import READ, WRITE, FileBlockStore, Guard, api_kind
from xianyu_core.item import DETAIL_API, EDIT_API
from xianyu_core.mtop import MtopClient
from xianyu_core.order import ADJUST_PRICE_API


def run(coro):
    return asyncio.run(coro)


class FakeClock:
    """假时钟：sleep 不真睡，只把时间往前拨。"""

    def __init__(self, now=1_000_000.0):
        self.now = now
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def make(clock, store=None, **kwargs):
    return Guard(store, clock=clock, sleep=clock.sleep, **kwargs)


def test_api_kind():
    assert api_kind(EDIT_API) == WRITE
    assert api_kind(ADJUST_PRICE_API) == WRITE
    assert api_kind(DETAIL_API) == READ


def test_waits_for_a_free_slot(clock):
    guard = make(clock, limits={READ: 2}, max_wait=60)
    run(guard.acquire(UNB, DETAIL_API))
    clock.now += 10
    run(guard.acquire(UNB, DETAIL_API))
    run(guard.acquire(UNB, DETAIL_API))  # 窗口里已经两次，等最早那次满 60 秒
    assert clock.slept == [50]


def test_too_long_a_wait_raises(clock):
    guard = make(clock, limits={WRITE: 1})
    run(guard.acquire(UNB, EDIT_API))
    clock.now += 5
    with pytest.raises(RateLimitedError, match="改动太频繁") as exc:
        run(guard.acquire(UNB, EDIT_API))
    assert exc.value.retry_after == 55
    assert clock.slept == []
    clock.now += 55
    run(guard.acquire(UNB, EDIT_API))


def test_counted_per_account_and_kind(clock):
    guard = make(clock, limits={READ: 1, WRITE: 1})
    run(guard.acquire(UNB, EDIT_API))
    run(guard.acquire(UNB, DETAIL_API))
    run(guard.acquire("43", EDIT_API))
    with pytest.raises(RateLimitedError):
        run(guard.acquire(UNB, DETAIL_API))


def test_trip_blocks_until_cooldown_ends(clock):
    guard = make(clock, cooldown=600)
    block = guard.trip(UNB, "RGV587")
    assert (block.since, block.until) == (clock.now, clock.now + 600)
    with pytest.raises(CircuitOpenError, match="10 分钟后自动恢复") as exc:
        run(guard.acquire(UNB, DETAIL_API))
    assert exc.value.until == block.until
    assert exc.value.reason == "RGV587"
    run(guard.acquire("43", DETAIL_API))  # 别的账号不受影响

    clock.now += 600
    assert guard.block(UNB) is None
    run(guard.acquire(UNB, DETAIL_API))


def test_unblock(clock):
    guard = make(clock)
    guard.trip(UNB, "RGV587")
    assert guard.unblock(UNB) is True
    assert guard.unblock(UNB) is False
    run(guard.acquire(UNB, DETAIL_API))


def test_tripped_while_waiting(clock):
    async def sleep_and_trip(seconds):
        await clock.sleep(seconds)
        guard.trip(UNB, "别的请求碰到了风控")

    guard = Guard(clock=clock, sleep=sleep_and_trip, limits={READ: 1}, max_wait=60)
    run(guard.acquire(UNB, DETAIL_API))
    with pytest.raises(CircuitOpenError):
        run(guard.acquire(UNB, DETAIL_API))


def test_block_survives_restart(tmp_path, clock):
    path = tmp_path / "guard.json"
    make(clock, FileBlockStore(path)).trip(UNB, "RGV587")
    other = make(clock, FileBlockStore(path))  # 另一个进程或重启以后
    assert other.block(UNB).reason == "RGV587"
    other.unblock(UNB)
    assert make(clock, FileBlockStore(path)).block(UNB) is None


def test_broken_state_file_stops_requests(tmp_path, clock):
    path = tmp_path / "guard.json"
    path.write_text("{oops")
    with pytest.raises(XianyuError, match="熔断状态文件损坏"):
        run(make(clock, FileBlockStore(path)).acquire(UNB, DETAIL_API))


def mtop_with(guard, ret):
    sent = []

    def handler(request):
        sent.append(request.url.params["api"])
        return httpx.Response(200, json={"ret": [ret]})

    credential = Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1"})
    client = MtopClient(credential, transport=httpx.MockTransport(handler), guard=guard.bind(UNB))
    return client, sent


def test_risk_control_trips_and_later_calls_are_not_sent(clock):
    guard = make(clock)
    client, sent = mtop_with(guard, "RGV587_ERROR::SM::哎哟喂,被挤爆啦")

    async def go():
        async with client:
            with pytest.raises(RiskControlError):
                await client.call(DETAIL_API)
            with pytest.raises(CircuitOpenError):
                await client.call(DETAIL_API)

    run(go())
    assert sent == [DETAIL_API]
    assert "RGV587" in guard.block(UNB).reason


def test_other_errors_do_not_trip(clock):
    guard = make(clock)
    client, _ = mtop_with(guard, "FAIL_SYS_SESSION_EXPIRED::Session过期")

    async def go():
        async with client:
            with pytest.raises(AuthRequiredError):
                await client.call(DETAIL_API)

    run(go())
    assert guard.block(UNB) is None


def test_cli_unblock(tmp_path, accounts):
    db_path = tmp_path / "xianyu.db"
    make_guard(db_path).trip(UNB, "RGV587")
    argv = ["--accounts", str(accounts.root), "--db", str(db_path), "accounts", "unblock", "-y"]
    assert cli.main(argv) == 0
    assert make_guard(db_path).block(UNB) is None


def test_check_during_block_is_risk_without_request(accounts, platform):
    guard = Guard()
    guarded = Accounts(accounts.root, transport=httpx.MockTransport(platform), guard=guard)
    guard.trip(UNB, "RGV587")
    status = run(guarded.check(UNB))
    assert status.state is LoginState.RISK
    assert "暂停" in status.message
    assert platform.calls == []
