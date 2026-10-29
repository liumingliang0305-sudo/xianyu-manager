import asyncio
import time
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core.credential import Credential
from xianyu_core.errors import AuthRequiredError, MtopError, RiskControlError
from xianyu_core.mtop import MtopClient
from xianyu_core.sign import mtop_sign

API = "mtop.taobao.idlemessage.pc.loginuser.get"


def run(coro):
    return asyncio.run(coro)


def form_of(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def make_client(handler, cookies=None) -> MtopClient:
    return MtopClient(Credential(cookies=cookies or {}), transport=httpx.MockTransport(handler))


def test_call_signs_request_and_returns_data():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": {"nick": "小鱼"}})

    async def go():
        async with make_client(handler, {"_m_h5_tk": "tok123_999", "unb": "42"}) as mtop:
            return await mtop.call(API, {"q": "键盘"})

    assert run(go()) == {"nick": "小鱼"}
    req = seen[0]
    params = req.url.params
    body = form_of(req)
    assert req.url.path == f"/h5/{API}/1.0/"
    assert params["appKey"] == "34839810"
    assert body["data"] == '{"q":"键盘"}'
    assert params["sign"] == mtop_sign("tok123", params["t"], body["data"])
    assert "_m_h5_tk=tok123_999" in req.headers["cookie"]


def test_emoji_is_utf16_escaped_like_web_and_signed_as_sent():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": {}})

    async def go():
        async with make_client(handler, {"_m_h5_tk": "tok123_999"}) as mtop:
            await mtop.call(API, {"desc": "九成新😀"})

    run(go())
    body = form_of(seen[0])
    assert body["data"] == '{"desc":"九成新\\ud83d\\ude00"}'
    assert seen[0].url.params["sign"] == mtop_sign("tok123", seen[0].url.params["t"], body["data"])


def test_token_expired_resigns_once_with_new_token():
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"]},
                headers={"set-cookie": "_m_h5_tk=newtok_1; Domain=.goofish.com; Path=/"},
            )
        return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": {"ok": True}})

    async def go():
        async with make_client(handler, {"_m_h5_tk": "oldtok_0"}) as mtop:
            data = await mtop.call(API)
            return data, mtop.credential

    data, credential = run(go())
    assert data == {"ok": True}
    assert len(seen) == 2
    retry = seen[1].url.params
    assert retry["sign"] == mtop_sign("newtok", retry["t"], "{}")
    assert credential.cookies["_m_h5_tk"] == "newtok_1"


def test_token_expired_without_new_token_raises_instead_of_looping():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ret": ["FAIL_SYS_TOKEN_EXOIRED::令牌过期"]})

    async def go():
        async with make_client(handler, {"_m_h5_tk": "tok_0"}) as mtop:
            await mtop.call(API)

    with pytest.raises(AuthRequiredError):
        run(go())
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("ret", "error"),
    [
        (["RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试"], RiskControlError),
        (["FAIL_SYS_USER_VALIDATE::需要验证"], RiskControlError),
        (["FAIL_SYS_SESSION_EXPIRED::Session过期"], AuthRequiredError),
        (["FAIL_BIZ_ITEM_NOT_EXIST::宝贝不存在"], MtopError),
    ],
)
def test_error_codes_are_classified(ret, error):
    def handler(request):
        return httpx.Response(200, json={"ret": ret})

    async def go():
        async with make_client(handler, {"_m_h5_tk": "tok_0"}) as mtop:
            await mtop.call(API)

    with pytest.raises(error) as exc_info:
        run(go())
    assert exc_info.value.ret == [ret[0].split("::")[0]]


def test_punish_redirect_is_risk_control():
    def handler(request):
        if request.url.host == "h5api.m.goofish.com":
            return httpx.Response(302, headers={"location": "https://www.goofish.com/punish?x=1"})
        return httpx.Response(200, text="<html>验证</html>")

    async def go():
        async with make_client(handler, {"_m_h5_tk": "tok_0"}) as mtop:
            await mtop.call(API)

    with pytest.raises(RiskControlError):
        run(go())


SESSION_EXPIRED = {"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]}
RENEW_OK = {"content": {"data": {"processFinished": True, "resultCode": 100}}}
KEEP_LOGIN = {"havana_lgc_exp": str(int((time.time() + 86400) * 1000))}


class ExpiringSession:
    """mtop 接口一直报会话过期，直到免密续期成功换发了 cookie2。"""

    def __init__(self, *, renew_ok=True, mtop_ret=SESSION_EXPIRED):
        self.renew_ok = renew_ok
        self.mtop_ret = mtop_ret
        self.mtop_calls: list[httpx.Request] = []
        self.renews: list[httpx.Request] = []

    def __call__(self, request):
        if request.url.host == "passport.goofish.com":
            self.renews.append(request)
            if not self.renew_ok:
                return httpx.Response(200, json={"content": {"data": {"resultCode": 0}}})
            return httpx.Response(
                200,
                json=RENEW_OK,
                headers={"set-cookie": "cookie2=fresh; Domain=.goofish.com; Path=/"},
            )
        self.mtop_calls.append(request)
        if "cookie2=fresh" in request.headers.get("cookie", ""):
            return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": {"ok": True}})
        return httpx.Response(200, json=self.mtop_ret)


def call_twice(fake, cookies):
    """同一个实例连调两次，返回两次的结果（异常也算结果）和这个实例。"""

    async def go():
        async with make_client(fake, cookies) as mtop:
            results = []
            for _ in range(2):
                try:
                    results.append(await mtop.call(API))
                except Exception as exc:
                    results.append(exc)
            return results, mtop

    return run(go())


def test_session_expired_renews_then_retries_with_new_cookie():
    fake = ExpiringSession()
    cookies = {"_m_h5_tk": "tok_1", "unb": "42", "cookie2": "old", **KEEP_LOGIN}

    async def go():
        async with make_client(fake, cookies) as mtop:
            return await mtop.call(API), mtop

    data, mtop = run(go())
    assert data == {"ok": True}
    assert len(fake.renews) == 1
    assert len(fake.mtop_calls) == 2
    retry = fake.mtop_calls[1].url.params
    assert retry["sign"] == mtop_sign("tok", retry["t"], "{}")  # 重签过
    assert mtop.renewal.ok
    assert mtop.credential.cookies["cookie2"] == "fresh"


def test_renew_failure_raises_auth_required_and_is_not_retried():
    fake = ExpiringSession(renew_ok=False)
    results, mtop = call_twice(fake, {"_m_h5_tk": "tok_1", "unb": "42", **KEEP_LOGIN})

    assert all(isinstance(r, AuthRequiredError) for r in results)
    assert "没有同意续期" in str(results[0])
    assert len(fake.renews) == 1  # 一个实例只试一次
    assert not mtop.renewal.ok


def test_no_keep_login_raises_without_renew_request():
    fake = ExpiringSession()
    results, mtop = call_twice(fake, {"_m_h5_tk": "tok_1", "unb": "42"})

    assert isinstance(results[0], AuthRequiredError)
    assert "保持登录" in str(results[0])
    assert fake.renews == []


@pytest.mark.parametrize(
    "ret", [["RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试"], ["FAIL_SYS_USER_VALIDATE::需要验证"]]
)
def test_risk_control_never_triggers_renew(ret):
    fake = ExpiringSession(mtop_ret={"ret": ret})
    results, mtop = call_twice(fake, {"_m_h5_tk": "tok_1", "unb": "42", **KEEP_LOGIN})

    assert all(isinstance(r, RiskControlError) for r in results)
    assert fake.renews == []
    assert mtop.renewal is None


def test_auto_renew_can_be_turned_off():
    fake = ExpiringSession()

    async def go():
        client = MtopClient(
            Credential(cookies={"_m_h5_tk": "tok_1", **KEEP_LOGIN}),
            transport=httpx.MockTransport(fake),
            auto_renew=False,
        )
        async with client as mtop:
            await mtop.call(API)

    with pytest.raises(AuthRequiredError):
        run(go())
    assert fake.renews == []
