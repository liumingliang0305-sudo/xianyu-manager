"""账号凭证：一个账号的全部 cookie，外加读写本地文件。

cookie 等同于账号密码。这里只做最基本的保护（文件权限 0600）；加密存储交给业务层，
落库时再做。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from xianyu_core.constants import COOKIE_DOMAIN, LOGIN_COOKIES
from xianyu_core.sign import h5_token


@dataclass
class Credential:
    cookies: dict[str, str] = field(default_factory=dict)
    saved_at: float | None = None

    @property
    def unb(self) -> str:
        """用户 ID。"""
        return self.cookies.get("unb", "")

    @property
    def h5_token(self) -> str:
        return h5_token(self.cookies.get("_m_h5_tk"))

    def missing_login_cookies(self) -> list[str]:
        return [name for name in LOGIN_COOKIES if not self.cookies.get(name)]

    def save(self, path: str | Path) -> Path:
        """原子写入，文件权限 0600。返回实际写入的路径。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.saved_at = time.time()
        payload = json.dumps(
            {"unb": self.unb, "saved_at": self.saved_at, "cookies": self.cookies},
            ensure_ascii=False,
            indent=2,
        )
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    @classmethod
    def load(cls, path: str | Path) -> Credential:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(cookies=dict(raw.get("cookies", {})), saved_at=raw.get("saved_at"))

    @classmethod
    def from_cookie_header(cls, header: str) -> Credential:
        """从浏览器复制的 `a=1; b=2` 形式的 cookie 串构造。"""
        cookies = {}
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name:
                cookies[name] = value
        return cls(cookies=cookies)


def cookies_to_jar(cookies: dict[str, str], jar: httpx.Cookies) -> None:
    for name, value in cookies.items():
        jar.set(name, value, domain=COOKIE_DOMAIN)


def jar_to_cookies(jar: httpx.Cookies) -> dict[str, str]:
    """把 cookie jar 压平成 name → value。

    同名 cookie 可能同时种在 `.goofish.com` 和某个子域上，以 `.goofish.com` 的为准，
    因为它是各子域共用的那份。
    """
    shared: dict[str, str] = {}
    others: dict[str, str] = {}
    for cookie in jar.jar:
        if cookie.value is None:
            continue
        target = shared if cookie.domain.lstrip(".") == COOKIE_DOMAIN.lstrip(".") else others
        target[cookie.name] = cookie.value
    return others | shared
