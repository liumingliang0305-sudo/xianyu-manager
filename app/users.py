"""管理后台的用户：密码、刷新令牌、插件令牌，和闲鱼账号归属。

用户由命令行 `xym users add` 建，没有注册入口。一个闲鱼账号只归一个用户，管理后台里每个用户
只看得到、只能操作自己名下的账号；商品、订单、消息这些表本来就按账号分开存，账号归属定了数据也就分开了。

密码用 argon2id 存。刷新令牌、插件令牌是随机串，库里只存 SHA-256，库被拿走也拿不到能用的令牌。
访问令牌（JWT）见 app/auth.py。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.db import Database, Row

# 刷新令牌多久没用就过期（每用一次往后顺延），和最长能用多久（到了要重新输密码）
REFRESH_IDLE = 7 * 24 * 3600.0
REFRESH_MAX_AGE = 30 * 24 * 3600.0
# 插件令牌带个前缀，接口一看就知道是插件令牌、不是访问令牌
PLUGIN_TOKEN_PREFIX = "xymp_"
MIN_PASSWORD = 8
MAX_PASSWORD = 128

_USERNAME = re.compile(r"[\w.-]{2,32}")
_hasher = PasswordHasher()


class AccountTakenError(Exception):
    """这个闲鱼账号已经归别的用户了。"""

    def __init__(self, account_id: str):
        super().__init__(
            f"闲鱼账号 {account_id} 已经绑定在别的用户名下了，"
            "要换用户找管理员（xym accounts assign）"
        )


@dataclass(frozen=True)
class User:
    id: int
    username: str
    token_version: int
    disabled: bool
    created_at: float
    plugin_token_at: float | None  # 插件令牌生成的时间，没有是 None

    @classmethod
    def from_row(cls, row: Row) -> User:
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


def normalize_username(text: str) -> str:
    """用户名不分大小写：2～32 个字母、数字、汉字或 _ . -"""
    name = text.strip().lower()
    if not _USERNAME.fullmatch(name):
        raise ValueError("用户名要 2～32 个字，只能用字母、数字、汉字和 _ . -")
    return name


def check_password_rule(password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError(f"密码要 {MIN_PASSWORD}～{MAX_PASSWORD} 位")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@functools.cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_hex(16))


def _verify(password_hash: str | None, password: str) -> bool:
    """没有这个用户时也拿一个假的哈希算一遍，从响应时间上看不出用户名存不存在。"""
    try:
        return _hasher.verify(password_hash or _dummy_hash(), password) and bool(password_hash)
    except (VerificationError, InvalidHashError):
        return False


class Users:
    def __init__(self, db: Database):
        self._db = db

    # ---- 用户 ----

    async def create(self, username: str, password: str) -> User:
        name = normalize_username(username)
        check_password_rule(password)
        if self.find(name):
            raise ValueError(f"用户 {name} 已经有了")
        password_hash = await asyncio.to_thread(_hasher.hash, password)
        row = self._db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (%s, %s, %s)"
            " RETURNING *",
            (name, password_hash, time.time()),
        ).fetchone()
        return User.from_row(row)

    def get(self, user_id: int) -> User | None:
        row = self._db.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        return User.from_row(row) if row else None

    def find(self, username: str) -> User | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE username = %s", (username.strip().lower(),)
        ).fetchone()
        return User.from_row(row) if row else None

    def list(self) -> list[User]:
        rows = self._db.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [User.from_row(row) for row in rows]

    async def authenticate(self, username: str, password: str) -> User | None:
        """用户名和密码对得上、用户没停用时返回用户。算哈希放到线程里，不卡住事件循环。"""
        row = self._db.execute(
            "SELECT * FROM users WHERE username = %s", (username.strip().lower(),)
        ).fetchone()
        password_hash = row["password_hash"] if row else None
        if not await asyncio.to_thread(_verify, password_hash, password[:MAX_PASSWORD]):
            return None
        if row["disabled"]:
            return None
        if _hasher.check_needs_rehash(password_hash):
            new_hash = await asyncio.to_thread(_hasher.hash, password)
            self._db.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, row["id"])
            )
        return User.from_row(row)

    async def set_password(self, user_id: int, password: str) -> User:
        """改密码。这个用户别处的登录全部作废：访问令牌立即失效，刷新令牌删掉。"""
        check_password_rule(password)
        password_hash = await asyncio.to_thread(_hasher.hash, password)
        with self._db.transaction():
            row = self._db.execute(
                "UPDATE users SET password_hash = %s, token_version = token_version + 1"
                " WHERE id = %s RETURNING *",
                (password_hash, user_id),
            ).fetchone()
            self._db.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))
        return User.from_row(row)

    def set_disabled(self, user_id: int, disabled: bool) -> None:
        """停用：登录、刷新、插件令牌都不认了，已经发出去的访问令牌立即失效。恢复以后要重新登录。"""
        with self._db.transaction():
            self._db.execute(
                "UPDATE users SET disabled = %s, token_version = token_version + 1 WHERE id = %s",
                (disabled, user_id),
            )
            self._db.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user_id,))

    # ---- 刷新令牌 ----

    def new_refresh_token(self, user_id: int) -> str:
        now = time.time()
        token = secrets.token_urlsafe(32)
        with self._db.transaction():
            # 顺手清掉过期的
            self._db.execute(
                "DELETE FROM refresh_tokens WHERE used_at < %s OR created_at < %s",
                (now - REFRESH_IDLE, now - REFRESH_MAX_AGE),
            )
            self._db.execute(
                "INSERT INTO refresh_tokens (token_hash, user_id, created_at, used_at)"
                " VALUES (%s, %s, %s, %s)",
                (_digest(token), user_id, now, now),
            )
        return token

    def use_refresh_token(self, token: str) -> User | None:
        """刷新令牌还有效（没过期、用户没停用）时返回用户，顺带把过期时间往后顺延。"""
        now = time.time()
        row = self._db.execute(
            "UPDATE refresh_tokens r SET used_at = %s FROM users u"
            " WHERE r.token_hash = %s AND u.id = r.user_id AND NOT u.disabled"
            " AND r.used_at >= %s AND r.created_at >= %s RETURNING u.*",
            (now, _digest(token), now - REFRESH_IDLE, now - REFRESH_MAX_AGE),
        ).fetchone()
        return User.from_row(row) if row else None

    def drop_refresh_token(self, token: str) -> None:
        self._db.execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (_digest(token),))

    # ---- 插件令牌：登录同步插件推登录时带上，只能用来推登录 ----

    def new_plugin_token(self, user_id: int) -> tuple[str, float]:
        """生成新的插件令牌，旧的作废。令牌只在这时候拿得到，库里只存哈希。"""
        token = PLUGIN_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute(
            "UPDATE users SET plugin_token_hash = %s, plugin_token_at = %s WHERE id = %s",
            (_digest(token), now, user_id),
        )
        return token, now

    def drop_plugin_token(self, user_id: int) -> None:
        self._db.execute(
            "UPDATE users SET plugin_token_hash = NULL, plugin_token_at = NULL WHERE id = %s",
            (user_id,),
        )

    def by_plugin_token(self, token: str) -> User | None:
        if not token.startswith(PLUGIN_TOKEN_PREFIX):
            return None
        row = self._db.execute(
            "SELECT * FROM users WHERE plugin_token_hash = %s AND NOT disabled", (_digest(token),)
        ).fetchone()
        return User.from_row(row) if row else None

    # ---- 闲鱼账号归属 ----

    def owner(self, account_id: str) -> int | None:
        row = self._db.execute(
            "SELECT user_id FROM account_owners WHERE account_id = %s", (account_id,)
        ).fetchone()
        return row["user_id"] if row else None

    def owns(self, user_id: int, account_id: str) -> bool:
        return self.owner(account_id) == user_id

    def account_ids(self, user_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT account_id FROM account_owners WHERE user_id = %s", (user_id,)
        ).fetchall()
        return {row["account_id"] for row in rows}

    def claim(self, account_id: str, user_id: int) -> None:
        """登录了一个闲鱼账号：还没有归属就归这个用户；已经归别人了抛 AccountTakenError。"""
        self._db.execute(
            "INSERT INTO account_owners (account_id, user_id, created_at) VALUES (%s, %s, %s)"
            " ON CONFLICT (account_id) DO NOTHING",
            (account_id, user_id, time.time()),
        )
        if self.owner(account_id) != user_id:
            raise AccountTakenError(account_id)

    def assign(self, account_id: str, user_id: int) -> None:
        """命令行用：把账号分给用户，原来归别人的直接转过来。"""
        self._db.execute(
            "INSERT INTO account_owners (account_id, user_id, created_at) VALUES (%s, %s, %s)"
            " ON CONFLICT (account_id) DO UPDATE"
            " SET user_id = EXCLUDED.user_id, created_at = EXCLUDED.created_at",
            (account_id, user_id, time.time()),
        )
