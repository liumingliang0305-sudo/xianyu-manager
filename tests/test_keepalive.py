import asyncio
import time
from collections import defaultdict

import httpx
import pytest
from fakes import UNB, keep_login_cookies
from fastapi.testclient import TestClient

from app import keepalive
from app.accounts import LoginState
from app.api import create_app
from xianyu_core.credential import Credential

LOGIN_USER_API = "mtop.taobao.idlemessage.pc.loginuser.get"


def check_due(accounts, max_age=1800):
    async def go():
        return await keepalive.check_due(accounts, defaultdict(asyncio.Lock), max_age=max_age)

    return asyncio.run(go())


def save_credential(accounts, **cookies):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1", "cookie2": "old", **cookies}).save(
        accounts.path(UNB)
    )


def login_checks(platform):
    return [api for api, _ in platform.calls].count(LOGIN_USER_API)


def test_unknown_login_is_checked_then_left_until_stale(accounts, platform):
    save_credential(accounts, **keep_login_cookies())

    assert check_due(accounts)[UNB].state is LoginState.VALID
    assert check_due(accounts) == {}  # 刚确认过
    assert login_checks(platform) == 1

    assert UNB in check_due(accounts, max_age=-1)  # 过了间隔再查
    assert login_checks(platform) == 2


def test_expired_session_is_renewed_right_away(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    platform.session_expired = True

    status = check_due(accounts)[UNB]

    assert status.state is LoginState.VALID
    assert status.renewed
    assert platform.renews == 1
    assert Credential.load(accounts.path(UNB)).cookies["cookie2"] == "renewed"


@pytest.mark.parametrize(("hours", "retried"), [(24, True), (-1, False)])
def test_expired_login_is_retried_only_when_renewal_can_work(
    accounts, platform, monkeypatch, hours, retried
):
    save_credential(accounts, **keep_login_cookies(hours))
    platform.session_expired = True
    platform.renew_ok = False
    assert check_due(accounts)[UNB].state is LoginState.EXPIRED

    # 刚失效，不马上再试
    assert check_due(accounts) == {}
    monkeypatch.setattr(keepalive, "EXPIRED_RETRY_AFTER", 0)
    # 过了重试间隔：保持登录还在才再试，已经到期就不试了
    assert (UNB in check_due(accounts)) is retried


def test_risk_control_is_left_alone(accounts, platform, monkeypatch):
    save_credential(accounts, **keep_login_cookies())
    platform.fail[LOGIN_USER_API] = "RGV587_ERROR::SM::哎哟喂"
    assert check_due(accounts)[UNB].state is LoginState.RISK

    monkeypatch.setattr(keepalive, "EXPIRED_RETRY_AFTER", 0)
    assert check_due(accounts, max_age=-1) == {}
    assert login_checks(platform) == 1


def test_renewable():
    now = time.time()
    future = str(int((now + 3600) * 1000))
    past = str(int((now - 3600) * 1000))

    assert keepalive.renewable(Credential(cookies={"havana_lgc_exp": future}), now)
    assert keepalive.renewable(
        Credential(cookies={"havana_lgc_exp": future, "sdkSilent": past}), now
    )
    # 限频没过
    assert not keepalive.renewable(
        Credential(cookies={"havana_lgc_exp": future, "sdkSilent": future}), now
    )
    # 保持登录到期了，或者没开
    assert not keepalive.renewable(Credential(cookies={"havana_lgc_exp": past}), now)
    assert not keepalive.renewable(Credential(cookies={}), now)


def test_serve_checks_logins_in_background(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    platform.session_expired = True
    app = create_app(
        db_path=":memory:",
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        web_dist=None,
        login_check_interval=3600,
    )
    with TestClient(app):
        deadline = time.monotonic() + 5
        while platform.renews == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
    # 启动时就查了一轮，会话过期当场续上；关闭时后台任务跟着停
    assert platform.renews == 1
    assert Credential.load(accounts.path(UNB)).cookies["cookie2"] == "renewed"
