"""mtop 接口客户端：签名、发请求、识别错误码。

- 响应里的 Set-Cookie 会自动进 cookie jar，`credential` 属性随时拿到最新 cookie；
- 平台说 token 过期并下发了新 `_m_h5_tk` 时，用新 token 重签一次；
- 风控错误一律抛 RiskControlError，不重试。
"""

from __future__ import annotations

import json
import logging
import re
import struct
import time
from typing import Any

import httpx

from xianyu_core._http import new_client
from xianyu_core.constants import APP_KEY, MTOP_HOST, WEB_HOST
from xianyu_core.credential import Credential, jar_to_cookies
from xianyu_core.errors import (
    AuthRequiredError,
    MtopError,
    RiskControlError,
    SignError,
    XianyuError,
)
from xianyu_core.sign import h5_token, mtop_sign

log = logging.getLogger(__name__)

# token 层失效：响应会带新的 _m_h5_tk，重签即可。EXOIRED 是平台原样拼写
TOKEN_CODES = frozenset(
    {"FAIL_SYS_TOKEN_EXOIRED", "FAIL_SYS_TOKEN_EXPIRED", "FAIL_SYS_TOKEN_EMPTY"}
)
# 会话层失效：重签没用，要重新登录
SESSION_CODES = frozenset({"FAIL_SYS_SESSION_EXPIRED", "FAIL_SYS_ILLEGAL_ACCESS"})
RISK_CODES = frozenset({"RGV587_ERROR", "FAIL_SYS_USER_VALIDATE"})
SIGN_CODES = frozenset({"FAIL_SYS_ILLEGAL_REQUEST", "FAIL_SYS_SIGN_ERROR"})

MTOP_HEADERS = {
    "accept": "application/json",
    "content-type": "application/x-www-form-urlencoded",
    "origin": WEB_HOST,
    "referer": f"{WEB_HOST}/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


_ASTRAL = re.compile(r"[\U00010000-\U0010FFFF]")


def _utf16_escape(match: re.Match[str]) -> str:
    units = struct.unpack("<2H", match.group().encode("utf-16-le"))
    return "".join(f"\\u{unit:04x}" for unit in units)


def encode_data(data: dict[str, Any] | None) -> str:
    """mtop 请求体里那串 JSON。签名和请求体必须用同一串。

    中文不转义，和浏览器 JSON.stringify 一致；emoji 这类 BMP 以外的字符写成
    `\\ud83d\\ude00` 形式，网页端的 mtop 中间件也是这么做的（商品描述里常有 emoji）。
    """
    raw = json.dumps(data or {}, separators=(",", ":"), ensure_ascii=False)
    return _ASTRAL.sub(_utf16_escape, raw)


def ret_codes(ret: Any) -> list[str]:
    """`["FAIL_SYS_TOKEN_EXOIRED::令牌过期"]` → `["FAIL_SYS_TOKEN_EXOIRED"]`"""
    if not isinstance(ret, list):
        return []
    return [str(item).split("::", 1)[0] for item in ret]


class MtopClient:
    """一个账号一个实例。

    用法：
        async with MtopClient(credential) as mtop:
            user = await mtop.call("mtop.taobao.idlemessage.pc.loginuser.get")
            credential = mtop.credential   # 拿回续期后的 cookie 存起来
    """

    def __init__(
        self,
        credential: Credential | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        # 传入 client 时共用它的 cookie jar（扫码登录就是这样用的），关闭由调用方负责
        self._owns_client = client is None
        self._client = client or new_client(
            credential.cookies if credential else None, transport=transport
        )

    @property
    def credential(self) -> Credential:
        return Credential(cookies=jar_to_cookies(self._client.cookies))

    async def call(
        self,
        api: str,
        data: dict[str, Any] | None = None,
        *,
        version: str = "1.0",
        spm_cnt: str = "a21ybx.home.0.0",
        extra_params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用一个 mtop 接口，成功时返回响应里的 `data`，失败抛 XianyuError 的子类。

        `extra_params` 覆盖或补充 URL 参数，`headers` 覆盖默认请求头（卖家工作台的接口要用）。
        """
        return await self._call(
            api, data, version, spm_cnt, extra_params, headers, retry_token=True
        )

    async def _call(
        self,
        api: str,
        data: dict[str, Any] | None,
        version: str,
        spm_cnt: str,
        extra_params: dict[str, str] | None,
        headers: dict[str, str] | None,
        *,
        retry_token: bool,
    ) -> dict[str, Any]:
        data_str = encode_data(data)
        t = str(int(time.time() * 1000))
        used_tk = self._m_h5_tk()
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": t,
            "sign": mtop_sign(h5_token(used_tk), t, data_str),
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": spm_cnt,
            **(extra_params or {}),
        }
        resp = await self._client.post(
            f"{MTOP_HOST}/h5/{api.lower()}/{version}/",
            params=params,
            data={"data": data_str},
            headers={**MTOP_HEADERS, **(headers or {})},
        )
        raw = self._parse(api, resp)
        codes = ret_codes(raw.get("ret"))
        if "SUCCESS" in codes:
            body = raw.get("data")
            return body if isinstance(body, dict) else {}

        if TOKEN_CODES & set(codes):
            if retry_token and self._m_h5_tk() != used_tk:
                log.debug("[%s] token 已更新，重签后重试一次", api)
                return await self._call(
                    api, data, version, spm_cnt, extra_params, headers, retry_token=False
                )
            raise AuthRequiredError(f"[{api}] 令牌失效且平台没有下发新令牌", ret=codes, raw=raw)
        raise _classify(api, codes, raw)

    def _m_h5_tk(self) -> str:
        return jar_to_cookies(self._client.cookies).get("_m_h5_tk", "")

    @staticmethod
    def _parse(api: str, resp: httpx.Response) -> dict[str, Any]:
        # 被风控时平台会跳到 punish 页或返回 HTML，不是 JSON
        if "punish" in str(resp.url):
            raise RiskControlError(f"[{api}] 被跳转到风控页：{resp.url}")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise XianyuError(f"[{api}] 返回的不是 JSON（HTTP {resp.status_code}）") from exc
        if not isinstance(raw, dict):
            raise XianyuError(f"[{api}] 返回的 JSON 不是对象")
        return raw

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> MtopClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def _classify(api: str, codes: list[str], raw: dict[str, Any]) -> XianyuError:
    joined = " | ".join(str(item) for item in raw.get("ret") or [])
    if RISK_CODES & set(codes) or "哎哟喂" in joined:
        return RiskControlError(f"[{api}] 触发风控：{joined}", ret=codes, raw=raw)
    if SESSION_CODES & set(codes):
        return AuthRequiredError(f"[{api}] 登录态失效：{joined}", ret=codes, raw=raw)
    if SIGN_CODES & set(codes):
        return SignError(f"[{api}] 签名或参数非法：{joined}", ret=codes, raw=raw)
    return MtopError(f"[{api}] 调用失败：{joined or '没有返回 ret'}", ret=codes, raw=raw)
