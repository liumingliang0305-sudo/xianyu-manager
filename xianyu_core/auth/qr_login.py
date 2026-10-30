"""纯 HTTP 扫码登录，不需要浏览器。

流程（和网页端登录框的行为一致）：
1. 调一次公开的 mtop 接口，拿到 `_m_h5_tk`；
2. 打开 `mini_login.htm`，从页面里的 `window.viewData.loginFormData` 取登录表单参数；
3. `generate.do` 取二维码，返回 `t`、`ck` 和二维码内容；
4. `query.do` 轮询：NEW → SCANED → CONFIRMED，也可能 EXPIRED / CANCELED；
5. 确认时平台可能要登录框弹"保持登录状态"（`dialogAction=keepLoginConfirm`），这里替用户选"保持"，
   提交平台给的 `dialogConfirmLoginUrl`；
6. 访问一次 `/im`，平台会在跳转链上下发完整的登录 cookie；
7. 再查一次保持登录，平台允许、还没开时打开（见 renew.py），会话过期后才能免密续期。
   5 和 7 失败都只记日志，不影响登录。

确认时如果响应带 `iframeRedirect`，说明平台要求额外身份验证，这里不自动处理，
抛 VerificationRequiredError 交给人工。

流程参考了 Ydisks-Xianyu-Helper（Apache-2.0）的 internal/xianyu/qrlogin
和 myfish（MIT）的 adapters/fish/api.py，代码是重新写的。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import qrcode
import qrcode.image.svg

from xianyu_core._http import new_client
from xianyu_core.constants import PASSPORT_HOST, USER_AGENT, WEB_HOST
from xianyu_core.credential import Credential, jar_to_cookies
from xianyu_core.errors import QRLoginError, VerificationRequiredError, XianyuError
from xianyu_core.mtop import MtopClient
from xianyu_core.renew import KeepLoginSettings, enable_keep_login

log = logging.getLogger(__name__)

MINI_LOGIN_URL = f"{PASSPORT_HOST}/mini_login.htm"
QR_GENERATE_URL = f"{PASSPORT_HOST}/newlogin/qrcode/generate.do"
QR_QUERY_URL = f"{PASSPORT_HOST}/newlogin/qrcode/query.do"
# 扫码确认后网页端会进入消息页，部分长登录 cookie 只在这一跳下发
LOGIN_LANDING_URL = f"{WEB_HOST}/im"
# 登录前拿 _m_h5_tk 用的公开接口（首页数据）
H5_TOKEN_API = "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get"

PASSPORT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": PASSPORT_HOST,
    "referer": f"{PASSPORT_HOST}/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
DOCUMENT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
}

# 平台连续返回 hasError 的容忍次数，超过就放弃
MAX_SERVER_ERRORS = 5

# 登录框"保持登录状态"弹窗：平台在确认结果里给 dialogAction 和各按钮要提交的地址
KEEP_LOGIN_DIALOG = "keepLoginConfirm"
KEEP_LOGIN_CONFIRM_CODE = "dialogConfirmLoginUrl"  # "保持"按钮；"不保持"是 dialogRefuseLoginUrl


class QRStatus(StrEnum):
    NEW = "NEW"
    SCANNED = "SCANED"  # 平台原样拼写
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: Any) -> QRStatus:
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class QRCode:
    content: str  # 二维码里编码的内容（一个链接），渲染成图给手机扫
    t: str
    ck: str

    def _qr(self) -> qrcode.QRCode:
        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(self.content)
        qr.make(fit=True)
        return qr

    def to_ascii(self, *, invert: bool = True) -> str:
        """终端字符画。深色背景的终端用 invert=True，浅色背景用 False。"""
        out = io.StringIO()
        self._qr().print_ascii(out=out, invert=invert)
        return out.getvalue()

    def to_svg(self) -> bytes:
        image = self._qr().make_image(image_factory=qrcode.image.svg.SvgPathImage)
        return image.to_string()


@dataclass(frozen=True)
class QRPoll:
    status: QRStatus
    has_error: bool = False
    needs_verification: bool = False
    verification_url: str = ""
    keep_login_url: str = ""  # 确认时平台要弹"保持登录状态"，选"保持"要提交的地址


class QRLogin:
    """一次扫码登录会话。

    用法：
        async with QRLogin() as login:
            qr = await login.start()
            print(qr.to_ascii())
            credential = await login.wait(on_status=print)
    """

    def __init__(
        self,
        *,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._client = new_client(transport=transport)
        self._form: dict[str, str] = {}
        # 登录框的 POST 都带 deviceId 和 pageTraceId：deviceId 取浏览器本地存的 APLUS_CNA，
        # 新浏览器是空的；pageTraceId 是登录页 eagleeye-trace 这个 meta 的值
        self._device: dict[str, str] = {"deviceId": "", "pageTraceId": ""}
        self.qr: QRCode | None = None
        # 登录完成后保持登录的状态；请求出错时是 None
        self.keep_login: KeepLoginSettings | None = None

    async def start(self) -> QRCode:
        """准备登录会话并取一张新二维码。可以重复调用来换码。"""
        await self._bootstrap_h5_token()
        form = await self._login_form()
        resp = await self._client.get(QR_GENERATE_URL, params=form, headers=PASSPORT_HEADERS)
        content = _json(resp, "generate.do").get("content") or {}
        data = content.get("data") or {}
        if not content.get("success") or not data.get("codeContent"):
            raise QRLoginError(f"获取二维码失败：{_brief(content)}", raw=content)

        # t 是毫秒时间戳，JSON 里可能是数字，要转成不带小数点的字符串
        t = data["t"]
        t = str(int(t)) if isinstance(t, int | float) else str(t)
        self.qr = QRCode(content=data["codeContent"], t=t, ck=str(data.get("ck", "")))
        self._form = form | {"t": self.qr.t, "ck": self.qr.ck}
        return self.qr

    async def poll(self) -> QRPoll:
        """查一次扫码状态。"""
        if not self._form:
            raise QRLoginError("还没有二维码，先调用 start()")
        form = self._form | {
            # 网页端二维码组件 query.do 带的浏览器环境字段
            "ua": "",
            "navlanguage": "zh-CN",
            "navUserAgent": USER_AGENT,
            "navPlatform": "MacIntel",
            "isIframe": "true",
            "documentReferer": LOGIN_LANDING_URL,
            "defaultView": "qrcode",
            **self._device,
        }
        resp = await self._client.post(QR_QUERY_URL, data=form, headers=PASSPORT_HEADERS)
        body = _json(resp, "query.do")
        if body.get("hasError"):
            return QRPoll(status=QRStatus.UNKNOWN, has_error=True)
        data = (body.get("content") or {}).get("data") or {}
        status = QRStatus.parse(data.get("qrCodeStatus"))
        if status is not QRStatus.CONFIRMED:
            return QRPoll(status=status)
        # 确认结果的形状还没在线上全部见过，记下字段名（不记值，里面可能有令牌）方便对照
        log.info(
            "扫码已确认：字段 %s，dialogAction=%s", sorted(data), data.get("dialogAction") or "-"
        )
        if data.get("iframeRedirect"):
            return QRPoll(
                status=status,
                needs_verification=True,
                verification_url=_verification_url(data),
            )
        return QRPoll(status=status, keep_login_url=_keep_login_url(data))

    async def wait(self, on_status: Callable[[QRStatus], None] | None = None) -> Credential:
        """一直轮询到登录完成，返回凭证。状态变化时回调 on_status。"""
        deadline = time.monotonic() + self.timeout
        last: QRStatus | None = None
        server_errors = 0
        while time.monotonic() < deadline:
            result = await self.poll()
            if result.has_error:
                server_errors += 1
                if server_errors >= MAX_SERVER_ERRORS:
                    raise QRLoginError("扫码状态接口连续报错", status=QRStatus.ERROR)
                await asyncio.sleep(self.poll_interval)
                continue
            server_errors = 0

            if result.status is not last and result.status is not QRStatus.UNKNOWN:
                last = result.status
                if on_status:
                    on_status(result.status)

            match result.status:
                case QRStatus.CONFIRMED if result.needs_verification:
                    raise VerificationRequiredError(
                        "扫码已确认，但平台要求额外身份验证",
                        verification_url=result.verification_url,
                    )
                case QRStatus.CONFIRMED:
                    return await self._finish(result.keep_login_url)
                case QRStatus.EXPIRED:
                    raise QRLoginError("二维码已过期", status=result.status)
                case QRStatus.CANCELED:
                    raise QRLoginError("已在手机上取消登录", status=result.status)
                case QRStatus.ERROR:
                    raise QRLoginError("平台返回扫码错误状态", status=result.status)
            await asyncio.sleep(self.poll_interval)
        raise QRLoginError("等待扫码超时", status=QRStatus.EXPIRED)

    async def _bootstrap_h5_token(self) -> None:
        # 只是为了让 jar 里先有 _m_h5_tk，和浏览器先打开首页一样。接口本身返回什么不重要：
        # 实测（2026-10-28）这个接口会报"缺少业务参数 piUrl"，但令牌已经在第一跳下发了
        try:
            await MtopClient(client=self._client, auto_renew=False).call(
                H5_TOKEN_API, {"bizScene": "home"}
            )
        except (XianyuError, httpx.HTTPError) as exc:
            if not jar_to_cookies(self._client.cookies).get("_m_h5_tk"):
                log.warning("登录前没拿到 _m_h5_tk，继续取码：%s", exc)

    async def _login_form(self) -> dict[str, str]:
        params = {
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": "false",
            "notKeepLogin": "false",
            "isMobile": "false",
            "qrCodeFirst": "false",
            "site": "77",
            "rnd": str(random.random()),
        }
        resp = await self._client.get(MINI_LOGIN_URL, params=params, headers=DOCUMENT_HEADERS)
        resp.raise_for_status()
        self._device["pageTraceId"] = _page_trace_id(resp.text)
        view_data = _extract_view_data(resp.text)
        form = view_data.get("loginFormData")
        if not isinstance(form, dict) or not form:
            raise QRLoginError("登录页里没有 loginFormData，页面结构可能改了")
        return {k: _form_value(v) for k, v in form.items()} | {"umidTag": "SERVER"}

    async def _finish(self, keep_login_url: str = "") -> Credential:
        if keep_login_url:
            await self._confirm_keep_login(keep_login_url)
        resp = await self._client.get(LOGIN_LANDING_URL, headers=DOCUMENT_HEADERS)
        if resp.status_code >= 400:
            log.warning("扫码后访问 /im 返回 HTTP %s，用已拿到的 cookie 继续", resp.status_code)
        credential = Credential(cookies=jar_to_cookies(self._client.cookies))
        if not credential.unb:
            raise QRLoginError(
                "扫码已确认，但没有拿到账号 cookie（unb）", status=QRStatus.CONFIRMED
            )
        await self._enable_keep_login()
        if not jar_to_cookies(self._client.cookies).get("_m_h5_tk"):
            await self._bootstrap_h5_token()
        return Credential(cookies=jar_to_cookies(self._client.cookies))

    async def _confirm_keep_login(self, url: str) -> None:
        """替用户在"保持登录状态"弹窗里点"保持"。失败只记日志，登录照常完成。"""
        try:
            resp = await self._client.post(url, data=self._device, headers=PASSPORT_HEADERS)
            data = (_json(resp, "保持登录弹窗").get("content") or {}).get("data") or {}
        except (QRLoginError, httpx.HTTPError) as exc:
            log.warning("提交保持登录弹窗失败：%s", exc)
            return
        code = data.get("resultCode")
        log.info("保持登录弹窗：字段 %s，resultCode=%s", sorted(data), code)
        # 网页端的判断：resultCode === 100 才算成功，接着按登录结果跳转
        if code != 100:
            log.warning("平台没有接受保持登录（resultCode=%s）%s", code, data.get("titleMsg") or "")
            return
        for flag, key in (("redirect", "redirectUrl"), ("parentRedirect", "parentRedirectUrl")):
            target = data.get(key)
            # 长登录 cookie 可能在跳转链上下发，所以跟一下；只跟本站的地址
            if data.get(flag) and isinstance(target, str) and _is_goofish(target):
                await self._client.get(target, headers=DOCUMENT_HEADERS)
                return

    async def _enable_keep_login(self) -> None:
        try:
            settings = await enable_keep_login(self._client)
        except (XianyuError, httpx.HTTPError) as exc:
            log.warning("登录成功了，但开启保持登录失败：%s", exc)
            return
        self.keep_login = settings
        # 在弹窗里开了的话，个人信息里的开关可能照样不给开（canOpenLongLogin=false），先看开没开
        if settings.enabled:
            return
        if not settings.can_open:
            log.warning("登录成功了，但平台不给这次登录开保持登录（canOpenLongLogin=false）")
        else:
            log.warning("登录成功了，但平台没有确认开启保持登录")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> QRLogin:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def _extract_view_data(html: str) -> dict[str, Any]:
    """取出页面里 `window.viewData = {...};` 的那个对象。

    用 raw_decode 而不是非贪婪正则：JSON 里的字符串可能包含 `};`。
    """
    marker = "window.viewData"
    idx = html.find(marker)
    if idx < 0:
        raise QRLoginError("登录页里没有 window.viewData，页面结构可能改了")
    start = html.find("{", idx)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as exc:
        raise QRLoginError("window.viewData 解析失败") from exc
    if not isinstance(obj, dict):
        raise QRLoginError("window.viewData 不是对象")
    return obj


def _form_value(value: Any) -> str:
    # 表单要和浏览器一致：布尔值是 true/false，null 是空串
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _page_trace_id(html: str) -> str:
    match = re.search(r'<meta[^>]*name="eagleeye-trace"[^>]*content="([^"]*)"', html)
    return match.group(1) if match else ""


def _keep_login_url(data: dict[str, Any]) -> str:
    """确认结果要弹"保持登录状态"时，"保持"按钮提交的地址。不弹，或者地址不在登录域名上时是空串。"""
    if data.get("dialogAction") != KEEP_LOGIN_DIALOG:
        return ""
    payload = data.get("feedbackPayload")
    urls = payload.get("dynamicUrl") if isinstance(payload, dict) else None
    url = urls.get(KEEP_LOGIN_CONFIRM_CODE) if isinstance(urls, dict) else None
    if not isinstance(url, str) or not url.strip():
        log.warning('平台要弹保持登录，但没给"保持"按钮的地址')
        return ""
    url = urljoin(f"{PASSPORT_HOST}/", url.strip())
    parts = urlsplit(url)
    # 请求会带上全部登录 cookie，只发给登录域名
    if parts.scheme != "https" or parts.hostname != urlsplit(PASSPORT_HOST).hostname:
        log.warning("保持登录的地址不在登录域名上，不提交：%s", parts.hostname)
        return ""
    return url


def _is_goofish(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "goofish.com" or host.endswith(".goofish.com"))


def _verification_url(data: dict[str, Any]) -> str:
    for key in ("iframeRedirectUrl", "url", "redirectUrl"):
        candidate = data.get(key)
        if isinstance(candidate, str) and candidate.strip():
            candidate = candidate.strip()
            if candidate.startswith("//"):
                return "https:" + candidate
            return urljoin(QR_QUERY_URL, candidate)
    return ""


def _json(resp: httpx.Response, name: str) -> dict[str, Any]:
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError as exc:
        raise QRLoginError(f"{name} 返回的不是 JSON（HTTP {resp.status_code}）") from exc
    if not isinstance(body, dict):
        raise QRLoginError(f"{name} 返回的 JSON 不是对象")
    return body


def _brief(content: dict[str, Any]) -> str:
    text = json.dumps(content, ensure_ascii=False)
    return text if len(text) <= 200 else text[:200] + "…"
