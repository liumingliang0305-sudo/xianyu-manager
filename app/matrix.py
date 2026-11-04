"""账号矩阵：一个用户名下所有闲鱼账号放在一起看、一起管。

- 资料：昵称、头像、IP 属地、卖家信用、粉丝数，从平台个人主页拉（xianyu_core/profile.py），存进
  account_profiles。登录有效时才拉，拉过的 PROFILE_TTL 以内不再拉；手动"重新检查登录态"时顺带重拉；
- 备注：自己给账号起的名字（"主号"、"数码号"），平台上看不到；
- 概况：在售商品、待发货订单、未读消息几个数，全从本地库里数，不请求平台。

移除账号见 app/api.py 的 DELETE /api/accounts/{id}：删凭证和归属，资料、备注跟着删；
本地的商品、订单、消息留着，重新登录这个账号后还在。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from app.accounts import Accounts
from app.db import Database, Row
from xianyu_core import XianyuError
from xianyu_core.order import SOLD, TO_SHIP
from xianyu_core.profile import Profile, fetch_profile

log = logging.getLogger(__name__)

# 资料多久重拉一次。昵称、头像不常改，一天一次够了
PROFILE_TTL = 24 * 3600
# 拉失败了多久以后再试，免得平台一直出错时每拉一次账号列表就请求一次
PROFILE_RETRY = 30 * 60
MAX_REMARK = 20


@dataclass(frozen=True)
class AccountProfile:
    nick: str
    avatar: str
    ip_location: str
    seller_credit: str
    followers: int | None
    fetched_at: float | None
    remark: str

    @classmethod
    def from_row(cls, row: Row) -> AccountProfile:
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


EMPTY = AccountProfile("", "", "", "", None, None, "")


@dataclass(frozen=True)
class AccountStats:
    on_sale: int  # 在售商品（主页上还在的）
    to_ship: int  # 卖出的、待发货的订单
    unread: int  # 未读消息（网页版显示的会话里的红点数加起来）


ZERO = AccountStats(0, 0, 0)


def normalize_remark(text: str) -> str:
    remark = " ".join(text.split())
    if len(remark) > MAX_REMARK:
        raise ValueError(f"备注最多 {MAX_REMARK} 个字")
    return remark


class AccountMatrix:
    def __init__(self, db: Database, accounts: Accounts):
        self._db = db
        self._accounts = accounts
        self._failed_at: dict[str, float] = {}  # 上次拉资料失败的时间，只放内存

    # ---- 资料和备注 ----

    def profile(self, account_id: str) -> AccountProfile:
        row = self._db.execute(
            "SELECT * FROM account_profiles WHERE account_id = %s", (account_id,)
        ).fetchone()
        return AccountProfile.from_row(row) if row else EMPTY

    def profile_due(self, account_id: str, now: float | None = None) -> bool:
        """该拉资料了：没拉到过或者过了 PROFILE_TTL，而且上次失败已经过了 PROFILE_RETRY。"""
        now = time.time() if now is None else now
        if now - self._failed_at.get(account_id, 0) < PROFILE_RETRY:
            return False
        fetched_at = self.profile(account_id).fetched_at
        return fetched_at is None or now - fetched_at >= PROFILE_TTL

    async def refresh_profile(self, account_id: str) -> bool:
        """从平台拉一次资料存下来。调用方要拿着这个账号的锁。

        拉不到（网络、平台出错、登录失效）只记日志、返回 False：资料只是让列表好认，
        不能因为它让账号列表出错。
        """
        try:
            async with self._accounts.mtop(account_id) as mtop:
                profile = await fetch_profile(mtop)
        except (XianyuError, httpx.HTTPError) as exc:
            self._failed_at[account_id] = time.time()
            log.warning("账号 %s：拉个人资料失败：%s", account_id, exc)
            return False
        self._failed_at.pop(account_id, None)
        self.save_profile(account_id, profile)
        return True

    def save_profile(self, account_id: str, profile: Profile, now: float | None = None) -> None:
        self._db.execute(
            "INSERT INTO account_profiles"
            " (account_id, nick, avatar, ip_location, seller_credit, followers, fetched_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (account_id) DO UPDATE SET nick = excluded.nick,"
            " avatar = excluded.avatar, ip_location = excluded.ip_location,"
            " seller_credit = excluded.seller_credit, followers = excluded.followers,"
            " fetched_at = excluded.fetched_at",
            (
                account_id,
                profile.nick,
                profile.avatar,
                profile.ip_location,
                profile.seller_credit,
                profile.followers,
                time.time() if now is None else now,
            ),
        )

    def set_remark(self, account_id: str, text: str) -> str:
        remark = normalize_remark(text)
        self._db.execute(
            "INSERT INTO account_profiles (account_id, remark) VALUES (%s, %s)"
            " ON CONFLICT (account_id) DO UPDATE SET remark = excluded.remark",
            (account_id, remark),
        )
        return remark

    def forget(self, account_id: str) -> None:
        """移除账号时删掉资料和备注。"""
        self._failed_at.pop(account_id, None)
        self._db.execute("DELETE FROM account_profiles WHERE account_id = %s", (account_id,))

    # ---- 概况 ----

    def stats(self, account_ids: Iterable[str]) -> dict[str, AccountStats]:
        """几个账号的概况，一个账号一行，没有数据的是 0。"""
        ids = list(account_ids)
        if not ids:
            return {}
        on_sale = self._counts(
            "SELECT account_id, count(*) AS n FROM items"
            " WHERE account_id = ANY(%s) AND status = 0 AND on_platform GROUP BY account_id",
            (ids,),
        )
        to_ship = self._counts(
            "SELECT account_id, count(*) AS n FROM orders"
            " WHERE account_id = ANY(%s) AND side = %s AND status = %s GROUP BY account_id",
            (ids, SOLD, TO_SHIP),
        )
        unread = self._counts(
            "SELECT account_id, sum(unread) AS n FROM conversations"
            " WHERE account_id = ANY(%s) AND visible GROUP BY account_id",
            (ids,),
        )
        return {
            a: AccountStats(on_sale.get(a, 0), to_ship.get(a, 0), unread.get(a, 0)) for a in ids
        }

    def _counts(self, sql: str, params: tuple) -> dict[str, int]:
        return {row["account_id"]: int(row["n"] or 0) for row in self._db.execute(sql, params)}
