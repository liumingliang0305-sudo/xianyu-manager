import time

import httpx
import pytest
from fakes import UNB, FakePlatform, FakeXianyu
from fastapi.testclient import TestClient

from app.api import create_app
from xianyu_core.auth.qr_login import H5_TOKEN_API
from xianyu_core.credential import Credential

QUERY = "/newlogin/qrcode/query.do"


def route(login: FakeXianyu, platform: FakePlatform):
    """扫码登录的请求给 login，登录以后的 mtop 接口和免密续期给 platform。"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "h5api.m.goofish.com":
            api = request.url.params["api"]
            return login(request) if api == H5_TOKEN_API else platform(request)
        if request.url.path == "/newlogin/silentHasLogin.do":
            return platform(request)
        return login(request)

    return httpx.MockTransport(handle)


@pytest.fixture
def fake_login():
    return FakeXianyu(["NEW"])


@pytest.fixture
def accounts_dir(tmp_path):
    return tmp_path / "accounts"


@pytest.fixture
def client(accounts_dir, platform, fake_login):
    app = create_app(
        db_path=":memory:",
        accounts_dir=accounts_dir,
        transport=route(fake_login, platform),
        interval=0,
        web_dist=None,
        login_poll_interval=0.01,
    )
    with TestClient(app) as client:
        yield client


def wait_for(client, session_id, phase):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        progress = client.get(f"/api/login/{session_id}").json()
        if progress["phase"] == phase:
            return progress
        time.sleep(0.01)
    raise AssertionError(f"等不到 {phase}，最后是 {progress}")


def test_scan_login_saves_credential_and_account_shows_up(client, accounts_dir, fake_login):
    assert client.get("/api/accounts").json() == {"accounts": []}

    started = client.post("/api/login").json()
    assert started["phase"] == "waiting"
    assert "<svg" in started["qr_svg"]
    session = started["id"]

    fake_login.statuses = ["SCANED"]
    wait_for(client, session, "scanned")
    assert not (accounts_dir / f"{UNB}.json").exists()

    fake_login.statuses = ["CONFIRMED"]
    done = wait_for(client, session, "done")
    assert done["account_id"] == UNB
    assert done["message"].startswith("保持登录到 ")

    credential = Credential.load(accounts_dir / f"{UNB}.json")
    assert credential.cookies["cookie2"] == "c2"
    assert credential.keep_login_until is not None
    accounts = client.get("/api/accounts").json()["accounts"]
    assert [a["id"] for a in accounts] == [UNB]
    assert accounts[0]["login"]["state"] == "valid"


def test_keep_login_dialog_is_confirmed_in_web_login(client, accounts_dir, fake_login):
    fake_login.keep_login_dialog = True
    session = client.post("/api/login").json()["id"]
    fake_login.statuses = ["CONFIRMED"]

    # 个人信息里的开关不给开（canOpenLongLogin=false），但弹窗里开成了
    assert wait_for(client, session, "done")["message"].startswith("保持登录到 ")
    assert Credential.load(accounts_dir / f"{UNB}.json").keep_login_until is not None


def test_relogin_replaces_expired_credential(client, accounts_dir, platform, fake_login):
    Credential(cookies={"unb": UNB, "cookie2": "old"}).save(accounts_dir / f"{UNB}.json")
    platform.session_expired = True
    assert client.get("/api/accounts").json()["accounts"][0]["login"]["state"] == "expired"

    session = client.post("/api/login").json()["id"]
    fake_login.statuses = ["CONFIRMED"]
    platform.session_expired = False  # 新扫到的会话是好的
    wait_for(client, session, "done")

    assert Credential.load(accounts_dir / f"{UNB}.json").cookies["cookie2"] == "c2"
    # 凭证文件更新了，记下的"已失效"作废，重新确认一次
    assert client.get("/api/accounts").json()["accounts"][0]["login"]["state"] == "valid"


@pytest.mark.parametrize(
    ("statuses", "phase"),
    [(["EXPIRED"], "expired"), (["CANCELED"], "canceled"), (["HAS_ERROR"], "failed")],
)
def test_login_failures(client, accounts_dir, fake_login, statuses, phase):
    session = client.post("/api/login").json()["id"]
    fake_login.statuses = statuses
    assert wait_for(client, session, phase)["message"]
    assert not accounts_dir.exists()


def test_verification_is_handed_to_human(client, accounts_dir, fake_login):
    fake_login.confirm_extra = {"iframeRedirect": True, "iframeRedirectUrl": "/iv/x"}
    session = client.post("/api/login").json()["id"]
    fake_login.statuses = ["CONFIRMED"]
    assert "身份验证" in wait_for(client, session, "verify")["message"]
    assert not accounts_dir.exists()


def test_new_qr_replaces_old_one(client):
    first = client.post("/api/login").json()["id"]
    second = client.post("/api/login").json()["id"]
    assert first != second

    resp = client.get(f"/api/login/{first}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "login_not_found"
    assert client.delete(f"/api/login/{first}").json() == {"ok": False}
    assert client.get(f"/api/login/{second}").json()["phase"] == "waiting"


def test_cancel_stops_polling(client, fake_login):
    session = client.post("/api/login").json()["id"]
    wait_for(client, session, "waiting")
    assert client.delete(f"/api/login/{session}").json() == {"ok": True}
    assert client.get(f"/api/login/{session}").status_code == 404

    polls = len(fake_login.find(QUERY))
    time.sleep(0.1)
    assert len(fake_login.find(QUERY)) == polls


# ---- 导入浏览器的登录 ----

BROWSER_COOKIE = (
    f"Cookie: unb={UNB}; cookie2=b2; _m_h5_tk=tk_1; sgcookie=sg;\nhavana_lgc_exp=4102444800000"
)


def import_login(client, cookie):
    return client.post("/api/login/import", json={"cookie": cookie})


def test_import_browser_login_with_keep_login(client, accounts_dir):
    resp = import_login(client, BROWSER_COOKIE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == UNB
    assert body["keep_login_until"] == 4102444800
    assert body["message"].startswith("保持登录到 ")

    credential = Credential.load(accounts_dir / f"{UNB}.json")
    assert credential.cookies["cookie2"] == "b2"
    assert credential.keep_login_until == 4102444800
    accounts = client.get("/api/accounts").json()["accounts"]
    assert accounts[0]["login"]["state"] == "valid"


def test_import_without_keep_login_says_so(client):
    body = import_login(client, f"unb={UNB}; cookie2=b2").json()
    assert body["keep_login_until"] is None
    assert "没有开保持登录" in body["message"]


@pytest.mark.parametrize(
    ("cookie", "hint"),
    [
        (f"unb={UNB}; _m_h5_tk=tk_1", "cookie2"),
        ("cookie2=b2", "unb"),
        ("unb=abc; cookie2=b2", "unb"),
    ],
)
def test_import_rejects_incomplete_cookie(client, accounts_dir, cookie, hint):
    resp = import_login(client, cookie)
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"
    assert hint in resp.json()["message"]
    assert "b2" not in resp.json()["message"]  # 不把 cookie 值写进消息
    assert not accounts_dir.exists()


def test_import_expired_cookie_is_rejected_without_renewing(client, accounts_dir, platform):
    Credential(cookies={"unb": UNB, "cookie2": "old"}).save(accounts_dir / f"{UNB}.json")
    platform.session_expired = True

    resp = import_login(client, BROWSER_COOKIE)
    assert resp.status_code == 401
    # 插件会反复推同一份过期的 cookie，替它续期就绕开了 sdkSilent 限频
    assert platform.renews == 0
    assert Credential.load(accounts_dir / f"{UNB}.json").cookies["cookie2"] == "old"


def test_import_same_login_again_does_not_ask_platform(client, platform):
    assert import_login(client, BROWSER_COOKIE).json()["changed"] is True
    calls = len(platform.calls)

    # 插件推过来的同一份登录，只是 _m_h5_tk 之类的变了
    again = import_login(client, BROWSER_COOKIE.replace("tk_1", "tk_2"))
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert again.json()["message"].startswith("保持登录到 ")
    assert len(platform.calls) == calls

    changed = import_login(client, BROWSER_COOKIE.replace("cookie2=b2", "cookie2=b3"))
    assert changed.json()["changed"] is True
    assert len(platform.calls) == calls + 1


def test_import_cookie_of_another_account_is_rejected(client, accounts_dir):
    resp = import_login(client, "unb=43; cookie2=b2")
    assert resp.status_code == 401
    assert not (accounts_dir / "43.json").exists()
