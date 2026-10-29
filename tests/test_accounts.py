import asyncio
import json

import pytest
from fakes import UNB, keep_login_cookies

from app.accounts import LoginState
from app.db import connect
from app.items import ItemService
from xianyu_core.credential import Credential
from xianyu_core.errors import AuthRequiredError


def run(coro):
    return asyncio.run(coro)


def save_credential(accounts, **cookies):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1", "cookie2": "old", **cookies}).save(
        accounts.path(UNB)
    )


def stored_cookies(accounts):
    return json.loads(accounts.path(UNB).read_text())["cookies"]


def test_renewed_cookies_are_written_back(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    platform.session_expired = True
    service = ItemService(connect(":memory:"), accounts, interval=0)

    result = run(service.sync(UNB))

    assert sorted(result.added) == ["1001", "1002"]
    assert platform.renews == 1
    assert stored_cookies(accounts)["cookie2"] == "renewed"
    status = accounts.login_status(UNB)
    assert status.state is LoginState.VALID
    assert status.renewed


def test_check_valid(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    status = run(accounts.check(UNB))

    assert status.state is LoginState.VALID
    assert not status.renewed
    assert status.keep_login_until is not None
    assert platform.renews == 0
    assert [api for api, _ in platform.calls] == ["mtop.taobao.idlemessage.pc.loginuser.get"]


def test_check_renews_expired_session(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    platform.session_expired = True

    status = run(accounts.check(UNB))

    assert status.state is LoginState.VALID
    assert status.renewed
    assert stored_cookies(accounts)["cookie2"] == "renewed"


@pytest.mark.parametrize("keep_login", [True, False])
def test_check_expired_when_renew_fails(accounts, platform, keep_login):
    save_credential(accounts, **(keep_login_cookies() if keep_login else {}))
    platform.session_expired = True
    platform.renew_ok = False

    status = run(accounts.check(UNB))

    assert status.state is LoginState.EXPIRED
    assert platform.renews == (1 if keep_login else 0)
    # 失效的登录态不自动重查，免得反复续期
    assert accounts.login_status(UNB, max_age=0) == status


def test_check_risk_control_does_not_renew(accounts, platform):
    save_credential(accounts, **keep_login_cookies())
    platform.fail["mtop.taobao.idlemessage.pc.loginuser.get"] = "RGV587_ERROR::SM::哎哟喂"

    status = run(accounts.check(UNB))

    assert status.state is LoginState.RISK
    assert platform.renews == 0


def test_login_status_is_rechecked_after_relogin_or_timeout(accounts, platform):
    status = run(accounts.check(UNB))
    assert accounts.login_status(UNB) == status
    assert accounts.login_status(UNB, max_age=-1) is None  # 有效的过一阵要重新确认

    save_credential(accounts)  # 重新扫码后凭证文件换了
    assert accounts.login_status(UNB) is None


def test_items_error_updates_login_status(accounts, platform):
    platform.session_expired = True
    service = ItemService(connect(":memory:"), accounts, interval=0)

    with pytest.raises(AuthRequiredError):
        run(service.fetch_detail(UNB, "1001"))
    assert accounts.login_status(UNB).state is LoginState.EXPIRED
