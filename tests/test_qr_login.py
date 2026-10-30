import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from fakes import FakeXianyu

from xianyu_core.auth import QRLogin, QRStatus
from xianyu_core.credential import Credential
from xianyu_core.errors import QRLoginError, VerificationRequiredError


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

    body = fake.find("/newlogin/qrcode/query.do")[0].content.decode()
    query = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
    assert query["t"] == "1761600000123"
    assert query["ck"] == "ck-1"
    assert query["_csrf_token"] == "csrf-abc"
    assert query["defaultView"] == "qrcode"
    # 登录框的 POST 都带这两个；新浏览器没有 APLUS_CNA，deviceId 是空的
    assert query["pageTraceId"] == "trace-1"
    assert query["deviceId"] == ""


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


def test_keep_login_dialog_is_confirmed():
    fake = FakeXianyu(["SCANED", "CONFIRMED"], keep_login_dialog=True)
    keep_login, credential = keep_login_of(fake)

    assert keep_login.enabled
    assert credential.keep_login_until == 4102444800
    confirm = fake.find("/newlogin/keepLoginConfirm.do")
    assert [r.url.params["choice"] for r in confirm] == ["keep"]
    assert confirm[0].url.host == "passport.goofish.com"
    assert parse_qs(confirm[0].content.decode(), keep_blank_values=True) == {
        "deviceId": [""],
        "pageTraceId": ["trace-1"],
    }
    # 弹窗里开了就不再去动个人信息里的开关
    assert not fake.find("/ac/account/setLoginSettings.do")
    paths = [r.url.path for r in fake.requests]
    assert paths.index("/newlogin/keepLoginConfirm.do") < paths.index("/im")


def test_keep_login_dialog_rejected_does_not_fail_login():
    fake = FakeXianyu(["CONFIRMED"], keep_login_dialog=True)
    fake.dialog_result = {"resultCode": 14036, "titleMsg": "系统繁忙"}
    keep_login, credential = keep_login_of(fake)

    assert credential.unb == "42"
    assert not keep_login.enabled
    assert credential.keep_login_until is None


@pytest.mark.parametrize(
    ("url", "followed"),
    [("https://www.goofish.com/im?from=keep", True), ("https://evil.example/x", False)],
)
def test_keep_login_dialog_redirect_only_follows_goofish(url, followed):
    fake = FakeXianyu(["CONFIRMED"], keep_login_dialog=True)
    fake.dialog_result = {"resultCode": 100, "redirect": True, "redirectUrl": url}
    keep_login_of(fake)
    assert any(str(r.url) == url for r in fake.requests) is followed


@pytest.mark.parametrize(
    "url", ["https://evil.example/keep", "http://passport.goofish.com/keep", "", None]
)
def test_keep_login_dialog_url_must_be_on_passport(url):
    fake = FakeXianyu(
        ["CONFIRMED"],
        keep_login_dialog=True,
        confirm_extra={"feedbackPayload": {"dynamicUrl": {"dialogConfirmLoginUrl": url}}},
    )
    keep_login, credential = keep_login_of(fake)

    assert credential.unb == "42"
    assert not keep_login.enabled
    assert [r.url.path for r in fake.requests].count("/keep") == 0
    assert not fake.find("/newlogin/keepLoginConfirm.do")


def test_keep_login_failure_does_not_fail_login():
    fake = FakeXianyu(["CONFIRMED"], keep_login=False)
    keep_login, credential = keep_login_of(fake)

    assert keep_login is None
    assert credential.unb == "42"
    assert credential.keep_login_until is None
