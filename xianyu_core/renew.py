"""登录态续期：保持登录开关和免密续期，都在 passport.goofish.com。

- 保持登录：网页端个人信息弹窗里"保存登录信息"的开关。打开后平台下发长登录标记
  `havana_lgc_exp` 或 `cookie3_bak_exp`（毫秒时间戳），免密续期要靠它；
- 免密续期：网页端 goofish-auto-login 插件（`window.lib.refreshAutoLogin`）在会话过期时做的事，
  一次 POST `silentHasLogin.do`，平台用 Set-Cookie 换发会话 cookie。插件的规矩照搬：
  `sdkSilent`（毫秒时间戳）没到期说明平台在限制续期频率，不发请求；没有未过期的长登录标记
  也不发请求；发请求前等 2 秒。

请求参数参考了 Ydisks-Xianyu-Helper（Apache-2.0）的 internal/xianyu/renew，
并对照了网页端插件 https://o.alicdn.com/vip/goofish-auto-login/plugin.js，代码是重新写的。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from xianyu_core.constants import PASSPORT_HOST, WEB_HOST
from xianyu_core.credential import cookie_time, jar_to_cookies
from xianyu_core.errors import RiskControlError, XianyuError

log = logging.getLogger(__name__)

SILENT_LOGIN_URL = f"{PASSPORT_HOST}/newlogin/silentHasLogin.do"
SET_LOGIN_SETTINGS_URL = f"{PASSPORT_HOST}/ac/account/setLoginSettings.do"
QUERY_LOGIN_SETTINGS_URL = f"{PASSPORT_HOST}/ac/account/queryLoginSettings.do"

# 插件取的是当前页面地址（去掉 query），扫码登录后落在消息页
DOCUMENT_REFERER = f"{WEB_HOST}/im"
# 插件发请求前固定等 2 秒；测试里改成 0
SILENT_RENEW_DELAY = 2.0

PASSPORT_HEADERS = {
    "accept": "*/*",
    "origin": WEB_HOST,
    "referer": DOCUMENT_REFERER,
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
SETTINGS_PARAMS = {"fromSite": "77", "appName": "xianyu", "bizEntrance": "web"}


class RenewOutcome(StrEnum):
    RENEWED = "renewed"
    FATIGUE = "fatigue"  # sdkSilent 没到期，平台在限制续期频率
    NO_KEEP_LOGIN = "no_keep_login"  # 没开保持登录，或者已经过期
    FAILED = "failed"  # 发了请求，平台没给续


@dataclass(frozen=True)
class RenewResult:
    outcome: RenewOutcome
    message: str

    @property
    def ok(self) -> bool:
        return self.outcome is RenewOutcome.RENEWED


@dataclass(frozen=True)
class KeepLoginSettings:
    can_open: bool  # 这个账号能不能开保持登录
    enabled: bool  # 当前会话是否已经开了


async def silent_renew(client: httpx.AsyncClient, *, delay: float | None = None) -> RenewResult:
    """免密续期一次。新 cookie 直接进 client 的 cookie jar。

    被跳到风控页时抛 RiskControlError；其他失败都放在返回值里，不抛异常。
    """
    cookies = jar_to_cookies(client.cookies)
    fatigue_until = cookie_time(cookies.get("sdkSilent"))
    if fatigue_until and fatigue_until > time.time():
        return RenewResult(
            RenewOutcome.FATIGUE,
            f"平台限制了续期频率，{_clock(fatigue_until)} 之前不能再续",
        )
    if _mode(cookies) is None:
        return RenewResult(RenewOutcome.NO_KEEP_LOGIN, "没有开启保持登录，或者保持登录已经过期")

    await asyncio.sleep(SILENT_RENEW_DELAY if delay is None else delay)
    # 和插件一样，等完再按当时的时间选分支
    mode = _mode(jar_to_cookies(client.cookies))
    if mode is None:
        return RenewResult(RenewOutcome.NO_KEEP_LOGIN, "保持登录刚好过期")
    params = {
        "documentReferer": DOCUMENT_REFERER,
        "appName": "xianyu",
        "appEntrance": "xianyu_sdkSilent",
        "fromSite": "0",
        **mode,
    }
    try:
        resp = await client.post(SILENT_LOGIN_URL, params=params, headers=PASSPORT_HEADERS)
    except httpx.HTTPError as exc:
        return RenewResult(RenewOutcome.FAILED, f"续期请求失败：{exc!r}")
    if "punish" in str(resp.url):
        raise RiskControlError(f"免密续期被跳转到风控页：{resp.url}")
    if not resp.is_success:
        return RenewResult(RenewOutcome.FAILED, f"续期接口返回 HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return RenewResult(RenewOutcome.FAILED, "续期接口返回的不是 JSON")
    data = _result_data(body)
    code = data.get("resultCode")
    # 插件的判断：processFinished === true && resultCode === 100
    if data.get("processFinished") is True and _is_number(code) and code == 100:
        return RenewResult(RenewOutcome.RENEWED, "免密续期成功")
    detail = data.get("resultMsg") or data.get("titleMsg") or ""
    return RenewResult(
        RenewOutcome.FAILED, f"平台没有同意续期（resultCode={code}）{detail}".strip()
    )


async def enable_keep_login(client: httpx.AsyncClient) -> KeepLoginSettings:
    """打开保持登录。新 cookie（长登录标记）直接进 client 的 cookie jar。

    和网页端一样先查一次：平台不给开（`can_open` 为 False，网页端这时不显示开关）或者已经开了，
    就不再提交。提交后再查一次，返回最终状态；平台明确说没开成时抛 XianyuError。
    """
    settings = await query_keep_login(client)
    if settings.enabled or not settings.can_open:
        return settings
    # status=0 是打开，1 是关闭
    resp = await client.post(
        SET_LOGIN_SETTINGS_URL,
        params=SETTINGS_PARAMS,
        data={"status": "0"},
        headers=PASSPORT_HEADERS,
    )
    body = _passport_json(resp, "setLoginSettings.do")
    data = body.get("data")
    if body.get("success") is not True and not (
        isinstance(data, dict) and data.get("success") is True
    ):
        reason = body.get("msgInfo") or "平台没有返回成功"
        raise XianyuError(f"开启保持登录失败：{reason}", raw=body)
    return await query_keep_login(client)


async def query_keep_login(client: httpx.AsyncClient) -> KeepLoginSettings:
    """查保持登录开没开。只读。"""
    resp = await client.post(
        QUERY_LOGIN_SETTINGS_URL, params=SETTINGS_PARAMS, headers=PASSPORT_HEADERS
    )
    body = _passport_json(resp, "queryLoginSettings.do")
    value = _find_dict(body, "returnValue")
    if value is None:
        raise XianyuError("查询保持登录失败：响应里没有 returnValue", raw=body)
    return KeepLoginSettings(
        can_open=value.get("canOpenLongLogin") is True,
        enabled=value.get("hasLongTokenLogin") is True,
    )


def _mode(cookies: dict[str, str]) -> dict[str, str] | None:
    """按长登录标记选续期分支：havana 优先，其次 cookie3，都过期了就不续。"""
    now = time.time()
    havana = cookie_time(cookies.get("havana_lgc_exp"))
    if havana and havana > now:
        return {"ltl": "true"}
    cookie3 = cookie_time(cookies.get("cookie3_bak_exp"))
    if cookie3 and cookie3 > now:
        return {"skipSessionFilter": "true", "c2r": "true"}
    return None


def _result_data(body: Any) -> dict[str, Any]:
    """silentHasLogin 的结果在 `data.content.data`，或者 `content.data`。"""
    if not isinstance(body, dict):
        return {}
    outer = body.get("data")
    content = outer.get("content") if isinstance(outer, dict) else None
    if not content:
        content = body.get("content")
    data = content.get("data") if isinstance(content, dict) else None
    return data if isinstance(data, dict) else {}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _find_dict(obj: Any, key: str, depth: int = 0) -> dict[str, Any] | None:
    if not isinstance(obj, dict) or depth > 6:
        return None
    value = obj.get(key)
    if isinstance(value, dict):
        return value
    for child in obj.values():
        found = _find_dict(child, key, depth + 1)
        if found is not None:
            return found
    return None


def _passport_json(resp: httpx.Response, name: str) -> dict[str, Any]:
    if "punish" in str(resp.url):
        raise RiskControlError(f"{name} 被跳转到风控页：{resp.url}")
    if not resp.is_success:
        raise XianyuError(f"{name} 返回 HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise XianyuError(f"{name} 返回的不是 JSON") from exc
    if not isinstance(body, dict):
        raise XianyuError(f"{name} 返回的 JSON 不是对象")
    return body


def _clock(seconds: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(seconds))
