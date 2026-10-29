"""商品管理：同步到本地库、查询、改价、改描述、下架、删除。

每次改动都记进 item_ops，失败也记，方便事后查"系统到底动过什么"。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.accounts import Accounts
from xianyu_core.item import ItemApi, ItemCard, ItemDetail, cent_to_yuan

T = TypeVar("T")

ONLINE = 0


class PriceGuardError(ValueError):
    """改价幅度超过保护线。确认没输错的话加 force=True。"""


@dataclass
class ItemRecord:
    account_id: str
    item_id: str
    title: str
    price_cent: int
    status: int
    category_id: int | None
    pic_url: str
    want_count: int | None
    browse_count: int | None
    collect_count: int | None
    on_platform: bool
    first_seen_at: float
    synced_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ItemRecord:
        return cls(**{**dict(row), "on_platform": bool(row["on_platform"])})


@dataclass
class ItemOp:
    id: int
    account_id: str
    item_id: str
    action: str
    detail: dict[str, Any]
    ok: bool
    error: str | None
    created_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ItemOp:
        return cls(**{**dict(row), "detail": json.loads(row["detail"]), "ok": bool(row["ok"])})


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # 上次还在、这次列表里没有了（下架或删除）


class ItemService:
    """
    `interval`：连续调平台接口之间停几秒（翻页、逐个拉详情）。
    `max_change`：改价保护线。0.5 表示降价超过一半、或涨价超过一倍时要 force。
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        accounts: Accounts,
        *,
        interval: float = 1.5,
        max_change: float = 0.5,
    ):
        self._db = db
        self._accounts = accounts
        self._interval = interval
        self._max_change = max_change

    # ---- 同步和查询 ----

    async def sync(self, account_id: str, *, with_stats: bool = False) -> SyncResult:
        """把个人主页上的商品全部拉下来写进本地库。

        `with_stats` 时再逐个拉在售商品的详情，补上浏览数和收藏数（每个商品多一次请求）。
        """
        async with self._accounts.mtop(account_id) as mtop:
            api = ItemApi(mtop)
            cards = [c async for c in api.iter_items(account_id, interval=self._interval)]
            details: dict[str, ItemDetail] = {}
            if with_stats:
                for card in cards:
                    if card.status == ONLINE:
                        await asyncio.sleep(self._interval)
                        details[card.item_id] = await api.detail(card.item_id)

        now = time.time()
        known = {
            row["item_id"]: bool(row["on_platform"])
            for row in self._db.execute(
                "SELECT item_id, on_platform FROM items WHERE account_id = ?", (account_id,)
            )
        }
        result = SyncResult()
        with self._db:
            for card in cards:
                self._upsert(account_id, card, details.get(card.item_id), now)
                (result.updated if card.item_id in known else result.added).append(card.item_id)
            seen = {card.item_id for card in cards}
            result.gone = [i for i, on in known.items() if on and i not in seen]
            self._db.executemany(
                "UPDATE items SET on_platform = 0 WHERE account_id = ? AND item_id = ?",
                [(account_id, item_id) for item_id in result.gone],
            )
        return result

    def list(
        self, account_id: str, *, status: int | None = None, include_gone: bool = False
    ) -> list[ItemRecord]:
        sql = "SELECT * FROM items WHERE account_id = ?"
        args: list[Any] = [account_id]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if not include_gone:
            sql += " AND on_platform = 1"
        sql += " ORDER BY status, first_seen_at DESC"
        return [ItemRecord.from_row(r) for r in self._db.execute(sql, args)]

    def get(self, account_id: str, item_id: str) -> ItemRecord | None:
        row = self._db.execute(
            "SELECT * FROM items WHERE account_id = ? AND item_id = ?", (account_id, item_id)
        ).fetchone()
        return ItemRecord.from_row(row) if row else None

    async def fetch_detail(self, account_id: str, item_id: str) -> ItemDetail:
        """从平台拉一次实时详情，顺手更新本地库里的价格和计数。"""
        async with self._accounts.mtop(account_id) as mtop:
            detail = await ItemApi(mtop).detail(item_id)
        with self._db:
            self._db.execute(
                "UPDATE items SET price_cent = ?, status = ?, want_count = ?, browse_count = ?,"
                " collect_count = ? WHERE account_id = ? AND item_id = ?",
                (
                    detail.price_cent,
                    detail.status,
                    detail.want_count,
                    detail.browse_count,
                    detail.collect_count,
                    account_id,
                    item_id,
                ),
            )
        return detail

    def history(self, account_id: str, item_id: str | None = None, limit: int = 50) -> list[ItemOp]:
        sql = "SELECT * FROM item_ops WHERE account_id = ?"
        args: list[Any] = [account_id]
        if item_id:
            sql += " AND item_id = ?"
            args.append(item_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [ItemOp.from_row(r) for r in self._db.execute(sql, args)]

    # ---- 改动 ----

    async def change_price(
        self,
        account_id: str,
        item_id: str,
        price_cent: int,
        *,
        orig_price_cent: int | None = None,
        force: bool = False,
    ) -> int:
        """改售价（可同时改原价），返回改之前的售价。"""
        if price_cent <= 0:
            raise ValueError("售价必须大于 0")
        async with self._accounts.mtop(account_id) as mtop:
            api = ItemApi(mtop)
            detail = await api.edit_detail(item_id)
            old = int((detail.get("itemPriceDTO") or {}).get("priceInCent") or 0)
            if not force:
                self._check_price(old, price_cent)
            change = {"from": old, "to": price_cent}
            if orig_price_cent is not None:
                change["orig_to"] = orig_price_cent
            await self._record(
                account_id,
                item_id,
                "price",
                change,
                api.edit(
                    item_id, price_cent=price_cent, orig_price_cent=orig_price_cent, detail=detail
                ),
            )
        with self._db:
            self._db.execute(
                "UPDATE items SET price_cent = ? WHERE account_id = ? AND item_id = ?",
                (price_cent, account_id, item_id),
            )
        return old

    async def change_desc(self, account_id: str, item_id: str, desc: str) -> None:
        """改描述。标题由平台从描述里截，下次同步时更新。"""
        async with self._accounts.mtop(account_id) as mtop:
            api = ItemApi(mtop)
            detail = await api.edit_detail(item_id)
            old = (detail.get("itemTextDTO") or {}).get("desc") or ""
            await self._record(
                account_id,
                item_id,
                "desc",
                {"from": old, "to": desc},
                api.edit(item_id, desc=desc, detail=detail),
            )

    async def downshelf(self, account_id: str, item_id: str) -> None:
        async with self._accounts.mtop(account_id) as mtop:
            await self._record(
                account_id, item_id, "downshelf", {}, ItemApi(mtop).downshelf(item_id)
            )
        with self._db:
            self._db.execute(
                "UPDATE items SET on_platform = 0 WHERE account_id = ? AND item_id = ?",
                (account_id, item_id),
            )

    async def delete(self, account_id: str, item_id: str) -> None:
        """删除商品，平台上不可恢复。本地记录一并删掉，改动记录保留。"""
        async with self._accounts.mtop(account_id) as mtop:
            await self._record(account_id, item_id, "delete", {}, ItemApi(mtop).delete(item_id))
        with self._db:
            self._db.execute(
                "DELETE FROM items WHERE account_id = ? AND item_id = ?", (account_id, item_id)
            )

    # ---- 内部 ----

    def _check_price(self, old: int, new: int) -> None:
        if old <= 0:
            return
        low, high = old * (1 - self._max_change), old / (1 - self._max_change)
        if not low <= new <= high:
            raise PriceGuardError(
                f"价格从 {cent_to_yuan(old)} 元改到 {cent_to_yuan(new)} 元，变化超过保护线"
            )

    async def _record(
        self,
        account_id: str,
        item_id: str,
        action: str,
        detail: dict[str, Any],
        op: Awaitable[T],
    ) -> T:
        try:
            result = await op
        except Exception as exc:
            self._log_op(account_id, item_id, action, detail, error=str(exc) or type(exc).__name__)
            raise
        self._log_op(account_id, item_id, action, detail, error=None)
        return result

    def _log_op(
        self,
        account_id: str,
        item_id: str,
        action: str,
        detail: dict[str, Any],
        *,
        error: str | None,
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO item_ops (account_id, item_id, action, detail, ok, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    item_id,
                    action,
                    json.dumps(detail, ensure_ascii=False),
                    error is None,
                    error,
                    time.time(),
                ),
            )

    def _upsert(
        self, account_id: str, card: ItemCard, detail: ItemDetail | None, now: float
    ) -> None:
        self._db.execute(
            """
            INSERT INTO items (account_id, item_id, title, price_cent, status, category_id,
                               pic_url, want_count, browse_count, collect_count, on_platform,
                               first_seen_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (account_id, item_id) DO UPDATE SET
                title = excluded.title,
                price_cent = excluded.price_cent,
                status = excluded.status,
                category_id = excluded.category_id,
                pic_url = excluded.pic_url,
                want_count = excluded.want_count,
                browse_count = COALESCE(excluded.browse_count, items.browse_count),
                collect_count = COALESCE(excluded.collect_count, items.collect_count),
                on_platform = 1,
                synced_at = excluded.synced_at
            """,
            (
                account_id,
                card.item_id,
                card.title,
                card.price_cent,
                card.status,
                card.category_id,
                card.pic_url,
                detail.want_count if detail else card.want_count,
                detail.browse_count if detail else None,
                detail.collect_count if detail else None,
                now,
                now,
            ),
        )
