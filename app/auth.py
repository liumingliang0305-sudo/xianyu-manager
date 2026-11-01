"""管理后台的登录鉴权：访问令牌（JWT）和登录限次。

- 访问令牌：HS256 签名的 JWT，15 分钟有效。前端只放在内存里，每个请求带在
  `Authorization: Bearer` 头上。载荷里有用户 ID 和 token_version，改密码、停用时
  token_version 加一，之前发的立即作废（每个请求查一次用户）；
- 刷新令牌：随机串，放在 HttpOnly cookie 里，只发给 /api/auth，页面脚本读不到。
  访问令牌过期了拿它换新的，见 app/users.py；
- 签名密钥：data/auth.key，第一次启动时生成。删掉它再重启，发出去的访问令牌全部作废
  （刷新令牌还在，页面会自动换新的）。
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

import jwt

from app.users import User

KEY_PATH = Path("data/auth.key")
ACCESS_TTL = 15 * 60
ALGORITHM = "HS256"
# 同一个用户名 15 分钟里输错 5 次，就等这 15 分钟过去再试
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60.0


class NotSignedInError(Exception):
    """没登录，或者访问令牌过期、作废了。前端拿刷新令牌换一个新的再试。"""


class LoginFailedError(Exception):
    """用户名或密码不对。"""


class TooManyAttemptsError(Exception):
    pass


def load_key(path: str | Path = KEY_PATH) -> bytes:
    """签名密钥，没有就生成一个（32 字节，文件权限 0600）。"""
    path = Path(path)
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # 别的进程刚生成好
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex())
    return key


class AccessTokens:
    def __init__(self, key: bytes, *, ttl: int = ACCESS_TTL):
        self._key = key
        self.ttl = ttl

    def issue(self, user: User) -> str:
        now = int(time.time())
        claims = {"sub": str(user.id), "ver": user.token_version, "iat": now, "exp": now + self.ttl}
        return jwt.encode(claims, self._key, algorithm=ALGORITHM)

    def verify(self, token: str) -> tuple[int, int]:
        """签名对、没过期时返回 (用户 ID, token_version)。用户还在不在、版本对不对由调用方查。"""
        try:
            claims = jwt.decode(
                token, self._key, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
            )
            return int(claims["sub"]), int(claims.get("ver", -1))
        except jwt.ExpiredSignatureError as exc:
            raise NotSignedInError("登录过期了") from exc
        except (jwt.InvalidTokenError, ValueError) as exc:
            raise NotSignedInError("登录凭证不对") from exc


class LoginLimiter:
    """按用户名记输错的次数，防暴力猜密码。只在内存里，重启清零。

    管理后台通过 SSH 隧道访问，看到的来源地址都是 127.0.0.1，所以不按 IP 限。
    """

    def __init__(self, *, max_failures: int = MAX_FAILURES, window: float = FAILURE_WINDOW):
        self._max = max_failures
        self._window = window
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, username: str) -> None:
        now = time.time()
        failures = self._failures.get(username)
        if not failures:
            return
        while failures and failures[0] <= now - self._window:
            failures.popleft()
        if not failures:
            del self._failures[username]
        elif len(failures) >= self._max:
            minutes = max(1, round((failures[0] + self._window - now) / 60))
            raise TooManyAttemptsError(f"密码输错次数太多，{minutes} 分钟后再试")

    def failed(self, username: str) -> None:
        self._failures[username].append(time.time())

    def succeeded(self, username: str) -> None:
        self._failures.pop(username, None)
