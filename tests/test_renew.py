import asyncio
import time
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core._http import new_client
from xianyu_core.credential import jar_to_cookies
from xianyu_core.errors import RiskControlError, XianyuError
from xianyu_core.renew import RenewOutcome, enable_keep_login, silent_renew

SHARED = "Domain=.goofish.com; Path=/"
RENEW_OK = {"content": {"data": {"processFinished": True, "resultCode": 100}}}


def ms(hours: float) -> str:
    """hours 小时后的毫秒时间戳（负数是过去）。"""
    return str(int((time.time() + hours * 3600) * 1000))


def renew_with(handler, cookies):
    async def go():
        async with new_client(cookies, transport=httpx.MockTransport(handler)) as client:
            result = await silent_renew(client, delay=0)
            return result, jar_to_cookies(client.cookies)

    return asyncio.run(go())


def test_renew_success_uses_havana_branch_and_keeps_new_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"data": {"content": {"data": {"processFinished": True, "resultCode": 100}}}},
            headers=[
                ("set-cookie", f"cookie2=fresh; {SHARED}; HttpOnly"),
                ("set-cookie", f"sdkSilent={ms(1)}; {SHARED}"),
            ],
        )

    result, cookies = renew_with(handler, {"unb": "42", "cookie2": "old", "havana_lgc_exp": ms(24)})

    assert result.outcome is RenewOutcome.RENEWED
    assert result.ok
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == "/newlogin/silentHasLogin.do"
    assert list(req.url.params.items()) == [
        ("documentReferer", "https://www.goofish.com/im"),
        ("appName", "xianyu"),
        ("appEntrance", "xianyu_sdkSilent"),
        ("fromSite", "0"),
        ("ltl", "true"),
    ]
    assert req.headers["origin"] == "https://www.goofish.com"
    assert "cookie2=old" in req.headers["cookie"]
    assert cookies["cookie2"] == "fresh"
    assert "sdkSilent" in cookies


def test_cookie3_branch_when_havana_expired():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=RENEW_OK)

    result, _ = renew_with(
        handler, {"unb": "42", "havana_lgc_exp": ms(-1), "cookie3_bak_exp": ms(24)}
    )
    assert result.ok
    params = seen[0].url.params
    assert params["skipSessionFilter"] == "true"
    assert params["c2r"] == "true"
    assert "ltl" not in params


@pytest.mark.parametrize(
    ("cookies", "outcome"),
    [
        ({"unb": "42"}, RenewOutcome.NO_KEEP_LOGIN),
        ({"unb": "42", "havana_lgc_exp": ms(-1)}, RenewOutcome.NO_KEEP_LOGIN),
        ({"unb": "42", "havana_lgc_exp": "abc"}, RenewOutcome.NO_KEEP_LOGIN),
        # sdkSilent 没到期：平台在限制续期频率
        ({"unb": "42", "havana_lgc_exp": ms(24), "sdkSilent": ms(1)}, RenewOutcome.FATIGUE),
    ],
)
def test_skips_without_sending_request(cookies, outcome):
    def handler(request):
        raise AssertionError("不该发续期请求")

    result, _ = renew_with(handler, cookies)
    assert result.outcome is outcome
    assert not result.ok


def test_expired_fatigue_window_does_not_block():
    result, _ = renew_with(
        lambda request: httpx.Response(200, json=RENEW_OK),
        {"unb": "42", "havana_lgc_exp": ms(24), "sdkSilent": ms(-1)},
    )
    assert result.ok


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"content": {"data": {"processFinished": True, "resultCode": 3}}}),
        # 和插件一样，resultCode 必须是数字 100、processFinished 必须是 true
        httpx.Response(
            200, json={"content": {"data": {"processFinished": True, "resultCode": "100"}}}
        ),
        httpx.Response(
            200, json={"content": {"data": {"processFinished": "true", "resultCode": 100}}}
        ),
        httpx.Response(200, text="<html>登录</html>"),
        httpx.Response(500),
    ],
)
def test_platform_refusal_is_failed(response):
    result, _ = renew_with(lambda request: response, {"unb": "42", "havana_lgc_exp": ms(24)})
    assert result.outcome is RenewOutcome.FAILED


def test_punish_redirect_during_renew_is_risk_control():
    def handler(request):
        if request.url.host == "passport.goofish.com":
            return httpx.Response(302, headers={"location": "https://www.goofish.com/punish?x=1"})
        return httpx.Response(200, text="<html>验证</html>")

    with pytest.raises(RiskControlError):
        renew_with(handler, {"unb": "42", "havana_lgc_exp": ms(24)})


def keep_login_with(set_body, *, can_open=True, enabled=False):
    """跑一次 enable_keep_login。平台状态：提交成功后 hasLongTokenLogin 变成 true。"""
    state = {"enabled": enabled}
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/ac/account/setLoginSettings.do":
            state["enabled"] = set_body.get("success") is True
            return httpx.Response(
                200, json=set_body, headers={"set-cookie": f"havana_lgc_exp={ms(720)}; {SHARED}"}
            )
        assert request.url.path == "/ac/account/queryLoginSettings.do"
        value = {"canOpenLongLogin": can_open, "hasLongTokenLogin": state["enabled"]}
        return httpx.Response(200, json={"code": 0, "returnValue": value, "success": True})

    async def go():
        async with new_client({"unb": "42"}, transport=httpx.MockTransport(handler)) as client:
            settings = await enable_keep_login(client)
            return settings, jar_to_cookies(client.cookies)

    settings, cookies = asyncio.run(go())
    return settings, cookies, [r.url.path.rsplit("/", 1)[1] for r in seen], seen


def test_enable_keep_login_queries_sets_then_queries():
    settings, cookies, paths, seen = keep_login_with({"success": True})

    assert settings.enabled and settings.can_open
    assert "havana_lgc_exp" in cookies
    assert paths == ["queryLoginSettings.do", "setLoginSettings.do", "queryLoginSettings.do"]
    set_req = seen[1]
    assert dict(set_req.url.params) == {"fromSite": "77", "appName": "xianyu", "bizEntrance": "web"}
    assert parse_qs(set_req.content.decode()) == {"status": ["0"]}  # 0 是打开
    assert seen[0].content == b""


@pytest.mark.parametrize(("can_open", "enabled"), [(False, False), (True, True)])
def test_enable_keep_login_skips_set_when_not_offered_or_already_on(can_open, enabled):
    settings, _, paths, _ = keep_login_with({"success": True}, can_open=can_open, enabled=enabled)
    assert paths == ["queryLoginSettings.do"]  # 网页端这时也不会提交
    assert settings.can_open is can_open
    assert settings.enabled is enabled


def test_enable_keep_login_raises_with_platform_message():
    with pytest.raises(XianyuError, match="开启保持登录失败：操作太频繁"):
        keep_login_with({"success": False, "msgInfo": "操作太频繁"})
