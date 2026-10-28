"""mtop 接口签名。

    sign = md5(f"{token}&{t}&{appKey}&{data}")

token 是 cookie `_m_h5_tk` 下划线前的那段，t 是毫秒时间戳，data 是请求体里那串 JSON 原文。
没有 token 时（比如登录前第一次请求）用空字符串签，平台会返回 TOKEN_EMPTY 并下发新的 `_m_h5_tk`。
"""

from __future__ import annotations

import hashlib

from xianyu_core.constants import APP_KEY


def h5_token(m_h5_tk: str | None) -> str:
    """从 `_m_h5_tk`（形如 `<token>_<过期时间戳>`）里取出签名用的 token。"""
    if not m_h5_tk:
        return ""
    return m_h5_tk.split("_", 1)[0]


def mtop_sign(token: str, t: str, data: str, app_key: str = APP_KEY) -> str:
    return hashlib.md5(f"{token}&{t}&{app_key}&{data}".encode()).hexdigest()
