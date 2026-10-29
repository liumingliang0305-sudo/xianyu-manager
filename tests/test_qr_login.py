import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core.auth import QRLogin, QRStatus
from xianyu_core.credential import Credential
from xianyu_core.errors import QRLoginError, VerificationRequiredError

LOGIN_FORM = {
    "appName": "xianyu",
    "appEntrance": "web",
    "_csrf_token": "csrf-abc",
    "umidToken": "umid-xyz",
    "hsiz": "hs-1",
    "isMobile": False,
    "returnUrl": "",
    "fromSite": 77,
}
# 故意让 JSON 字符串里出现 "};"，验证解析不会提前截断
VIEW_DATA = {"loginFormData": LOGIN_FORM, "title": "登录};"}
MINI_LOGIN_HTML = (
    "<html><script>window.viewData = " + json.dumps(VIEW_DATA, ensure_ascii=False) + ";"
    "window.other = {};</script></html>"
)

SHARED = "Domain=.goofish.com; Path=/"


class FakeXianyu:
    """按 URL 模拟闲鱼各接口；query.do 按 statuses 顺序返回。"""

    def __init__(self, statuses, *, confirm_extra=None, keep_login=True):
        self.statuses = list(statuses)
        self.confirm_extra = confirm_extra or {}
        self.keep_login = keep_login  # False 时打开保持登录的接口报错
        self.long_login_on = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "h5api.m.goofish.com":
            return httpx.Response(
                200,
                json={"ret": ["FAIL_SYS_TOKEN_EMPTY::令牌为空"]},
                headers={"set-cookie": f"_m_h5_tk=boot_1; {SHARED}"},
            )
        if path == "/mini_login.htm":
            return httpx.Response(200, text=MINI_LOGIN_HTML)
        if path == "/newlogin/qrcode/generate.do":
            return httpx.Response(
                200,
                json={
                    "content": {
                        "success": True,
                        "data": {
                            "t": 1761600000123,
                            "ck": "ck-1",
                            "codeContent": "https://qr.example/x",
                        },
                    }
                },
            )
        if path == "/newlogin/qrcode/query.do":
            status = self.statuses.pop(0)
            if status == "HAS_ERROR":
                return httpx.Response(200, json={"hasError": True})
            data = {"qrCodeStatus": status}
            headers = []
            if status == "CONFIRMED":
                data |= self.confirm_extra
                headers = [
                    ("set-cookie", f"cookie2=c2; {SHARED}; HttpOnly"),
                    ("set-cookie", f"unb=42; {SHARED}"),
                ]
            return httpx.Response(200, json={"content": {"data": data}}, headers=headers)
        if host == "www.goofish.com" and path == "/im":
            return httpx.Response(
                200, text="<html>im</html>", headers=[("set-cookie", f"sgcookie=sg; {SHARED}")]
            )
        if path == "/ac/account/setLoginSettings.do":
            if not self.keep_login:
                return httpx.Response(500)
            self.long_login_on = True
            return httpx.Response(
                200,
                json={"success": True},
                headers=[("set-cookie", f"havana_lgc_exp=4102444800000; {SHARED}")],
            )
        if path == "/ac/account/queryLoginSettings.do":
            value = {"canOpenLongLogin": True, "hasLongTokenLogin": self.long_login_on}
            return httpx.Response(200, json={"returnValue": value, "success": True})
        return httpx.Response(404)

    def find(self, path):
        return [r for r in self.requests if r.url.path == path]


def login_with(fake, **kwargs):
    statuses_seen = []

    async def go():
        async with QRLogin(poll_interval=0, transport=httpx.MockTransport(fake), **kwargs) as login:
            qr = await login.start()
            credential = await login.wait(on_status=statuses_seen.append)
            return qr, credential

    qr, credential = asyncio.run(go())
    return qr, credential, statuses_seen


def keep_login_of(fake):
    async def go():
        async with QRLogin(poll_interval=0, transport=httpx.MockTransport(fake)) as login:
            await login.start()
            credential = await login.wait()
            return login.keep_login, credential

    return asyncio.run(go())


def test_full_login_flow_collects_cookies():
    fake = FakeXianyu(["NEW", "NEW", "SCANED", "CONFIRMED"])
    qr, credential, statuses = login_with(fake)

    assert qr.t == "1761600000123"  # 数字时间戳不能变成 1.76e12
    assert qr.content == "https://qr.example/x"
    assert statuses == [QRStatus.NEW, QRStatus.SCANNED, QRStatus.CONFIRMED]
    assert isinstance(credential, Credential)
    assert credential.unb == "42"
    assert credential.cookies["cookie2"] == "c2"
    assert credential.cookies["_m_h5_tk"] == "boot_1"
    assert credential.cookies["sgcookie"] == "sg"
    assert credential.missing_login_cookies() == []


def test_generate_and_query_carry_login_form():
    fake = FakeXianyu(["CONFIRMED"])
    login_with(fake)

    gen = fake.find("/newlogin/qrcode/generate.do")[0].url.params
    assert gen["_csrf_token"] == "csrf-abc"
    assert gen["isMobile"] == "false"  # 布尔值要和浏览器一样是小写
    assert gen["fromSite"] == "77"
    assert gen["umidTag"] == "SERVER"

    query = {
        k: v[0]
        for k, v in parse_qs(fake.find("/newlogin/qrcode/query.do")[0].content.decode()).items()
    }
    assert query["t"] == "1761600000123"
    assert query["ck"] == "ck-1"
    assert query["_csrf_token"] == "csrf-abc"
    assert query["defaultView"] == "qrcode"


def test_server_errors_are_retried():
    fake = FakeXianyu(["HAS_ERROR", "HAS_ERROR", "CONFIRMED"])
    _, credential, _ = login_with(fake)
    assert credential.unb == "42"


def test_too_many_server_errors_give_up():
    fake = FakeXianyu(["HAS_ERROR"] * 5)
    with pytest.raises(QRLoginError) as exc_info:
        login_with(fake)
    assert exc_info.value.status == QRStatus.ERROR


@pytest.mark.parametrize("status", ["EXPIRED", "CANCELED"])
def test_terminal_statuses_raise(status):
    fake = FakeXianyu(["NEW", status])
    with pytest.raises(QRLoginError) as exc_info:
        login_with(fake)
    assert exc_info.value.status == status


def test_verification_required_is_handed_to_human():
    fake = FakeXianyu(
        ["SCANED", "CONFIRMED"],
        confirm_extra={
            "iframeRedirect": True,
            "iframeRedirectUrl": "//passport.goofish.com/iv/mini/verify_modes.htm?x=1",
        },
    )
    with pytest.raises(VerificationRequiredError) as exc_info:
        login_with(fake)
    assert (
        exc_info.value.verification_url
        == "https://passport.goofish.com/iv/mini/verify_modes.htm?x=1"
    )
    assert not fake.find("/im")  # 需要验证时不能当成登录成功


def test_timeout():
    fake = FakeXianyu(["NEW"] * 1000)
    with pytest.raises(QRLoginError, match="超时"):
        login_with(fake, timeout=0.05)


def test_qr_renders_to_ascii_and_svg():
    fake = FakeXianyu(["CONFIRMED"])
    qr, _, _ = login_with(fake)
    assert len(qr.to_ascii().splitlines()) > 10
    assert qr.to_svg().startswith(b"<?xml") or b"<svg" in qr.to_svg()[:200]


def test_login_turns_on_keep_login():
    fake = FakeXianyu(["CONFIRMED"])
    keep_login, credential = keep_login_of(fake)

    assert keep_login.enabled
    assert credential.cookies["havana_lgc_exp"] == "4102444800000"
    assert credential.keep_login_until == 4102444800
    set_req = fake.find("/ac/account/setLoginSettings.do")[0]
    assert set_req.content == b"status=0"
    # 先落到 /im 拿齐 cookie，再开保持登录
    paths = [r.url.path for r in fake.requests]
    assert paths.index("/im") < paths.index("/ac/account/queryLoginSettings.do")


def test_keep_login_failure_does_not_fail_login():
    fake = FakeXianyu(["CONFIRMED"], keep_login=False)
    keep_login, credential = keep_login_of(fake)

    assert keep_login is None
    assert credential.unb == "42"
    assert credential.keep_login_until is None
