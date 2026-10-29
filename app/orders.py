"""订单管理：卖出的订单进本地库、查询、拉实时详情、给待付款订单改价。

订单怎么进本地库：
- 个人卖家在网页端拿不到卖出的订单列表，只能按订单号添加（从闲鱼 App「我卖出的」里复制），
  之后"同步"会逐个刷新还没结束的订单；
- 闲鱼专业卖家可以从卖家工作台整页拉。这条路没实测过，原始条目存进 orders.raw 备查。

改价记进 order_ops，失败也记。
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.accounts import Accounts
from app.items import check_price_change
from xianyu_core.errors import MtopError
from xianyu_core.order import (
    CLOSED,
    DONE,
    OTHER,
    OrderApi,
    OrderDetail,
    PriceField,
    SoldOrder,
)

T = TypeVar("T")

# 个人卖家调卖家工作台的订单列表时平台返回的错误码
NO_PERMISSION = "PERMISSION_EXCEPTION"

# 订单号是一长串数字；也认订单详情页的链接（...?orderId=<订单号>）
_ORDER_ID = re.compile(r"(?:orderId=|tid=)?(\d{10,})")

# 列表和单条查询共用。列表接口没给标题、图片时，用本地商品库里同一个商品的补上
_SELECT = """
SELECT o.account_id, o.order_id, o.item_id,
       COALESCE(NULLIF(o.title, ''), i.title, '') AS title,
       COALESCE(NULLIF(o.pic_url, ''), i.pic_url, '') AS pic_url,
       o.status, o.status_text, o.price_cent, o.quantity, o.buyer_id, o.buyer_nick,
       o.created_at, o.first_seen_at, o.synced_at
FROM orders o
LEFT JOIN items i ON i.account_id = o.account_id AND i.item_id = o.item_id
"""


@dataclass
class OrderRecord:
    account_id: str
    order_id: str
    item_id: str
    title: str
    pic_url: str
    status: str
    status_text: str
    price_cent: int | None
    quantity: int
    buyer_id: str
    buyer_nick: str
    created_at: float | None
    first_seen_at: float
    synced_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OrderRecord:
        return cls(**dict(row))


@dataclass
class OrderOp:
    id: int
    account_id: str
    order_id: str
    action: str
    detail: dict[str, Any]
    ok: bool
    error: str | None
    created_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OrderOp:
        return cls(**{**dict(row), "detail": json.loads(row["detail"]), "ok": bool(row["ok"])})


@dataclass
class OrderSyncResult:
    # list：从卖家工作台整页拉的；detail：没有列表权限，逐个刷新了本地还没结束的订单
    source: str = "list"
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # 本地已有、这次状态变了的
    failed: list[str] = field(default_factory=list)  # 逐个刷新时平台报错的


def parse_order_id(text: str) -> str:
    """用户粘贴的订单号或订单链接 → 订单号。"""
    match = _ORDER_ID.search(text.replace(" ", ""))
    if not match:
        raise ValueError("没找到订单号。在闲鱼 App「我卖出的」里点进订单，复制订单编号")
    return match.group(1)


class OrderService:
    """
    `interval`：连续调平台接口之间停几秒（翻页）。
    `max_change`：改价保护线，和商品改价一样按订单总价算。
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

    async def sync(self, account_id: str, *, max_pages: int = 50) -> OrderSyncResult:
        """专业卖家：把卖家工作台里卖出的订单全部拉下来（每页 20 条，最多翻 `max_pages` 页）。

        个人卖家没有这个列表（平台返回无权限），改成逐个刷新本地还没结束的订单。
        """
        async with self._accounts.mtop(account_id) as mtop:
            api = OrderApi(mtop)
            try:
                orders = [
                    o async for o in api.iter_sold(interval=self._interval, max_pages=max_pages)
                ]
            except MtopError as exc:
                if NO_PERMISSION not in exc.ret:
                    raise
                return await self._refresh_open(account_id, api)

        now = time.time()
        known = {
            row["order_id"]: row["status"]
            for row in self._db.execute(
                "SELECT order_id, status FROM orders WHERE account_id = ?", (account_id,)
            )
        }
        result = OrderSyncResult()
        with self._db:
            for order in orders:
                self._upsert(account_id, order, now)
                if order.order_id not in known:
                    result.added.append(order.order_id)
                    continue
                result.updated.append(order.order_id)
                if known[order.order_id] != order.status:
                    result.changed.append(order.order_id)
        return result

    def list(self, account_id: str, *, status: str | None = None) -> list[OrderRecord]:
        sql = _SELECT + " WHERE o.account_id = ?"
        args: list[Any] = [account_id]
        if status:
            sql += " AND o.status = ?"
            args.append(status)
        sql += " ORDER BY COALESCE(o.created_at, o.first_seen_at) DESC, o.order_id DESC"
        return [OrderRecord.from_row(r) for r in self._db.execute(sql, args)]

    def get(self, account_id: str, order_id: str) -> OrderRecord | None:
        row = self._db.execute(
            _SELECT + " WHERE o.account_id = ? AND o.order_id = ?", (account_id, order_id)
        ).fetchone()
        return OrderRecord.from_row(row) if row else None

    async def add(self, account_id: str, text: str) -> tuple[OrderRecord, bool]:
        """按订单号（或订单链接）添加一笔卖出的订单，返回 (本地记录, 是不是新加的)。

        拉一次详情确认订单存在、而且当前账号是卖家；已经在本地的就当作刷新一次。
        """
        order_id = parse_order_id(text)
        async with self._accounts.mtop(account_id) as mtop:
            detail = await OrderApi(mtop).detail(order_id)
        if detail.seller is False:
            raise ValueError(f"订单 {order_id} 是你买到的，不是卖出的")
        if not detail.title and not detail.status_title:
            raise ValueError(f"查不到订单 {order_id}，确认订单号没抄错")
        with self._db:
            before = self._save_detail(account_id, detail, time.time())
        record = self.get(account_id, order_id)
        assert record is not None
        return record, before is None

    def remove(self, account_id: str, order_id: str) -> bool:
        """从本地列表里移除（加错了的时候用），不动平台上的订单。改动记录保留。"""
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM orders WHERE account_id = ? AND order_id = ?", (account_id, order_id)
            )
        return cursor.rowcount > 0

    async def fetch_detail(self, account_id: str, order_id: str) -> OrderDetail:
        """从平台拉一次订单详情；本地有这笔订单的话顺手更新状态、标题和金额。"""
        async with self._accounts.mtop(account_id) as mtop:
            detail = await OrderApi(mtop).detail(order_id)
        if self.get(account_id, order_id):
            with self._db:
                self._save_detail(account_id, detail, time.time())
        return detail

    async def price_fields(self, account_id: str, order_id: str) -> list[PriceField]:
        """能改的几项价格和当前值。不是待付款的订单平台会报错或返回空。"""
        async with self._accounts.mtop(account_id) as mtop:
            return await OrderApi(mtop).price_fields(order_id)

    def history(
        self, account_id: str, order_id: str | None = None, limit: int = 50
    ) -> list[OrderOp]:
        sql = "SELECT * FROM order_ops WHERE account_id = ?"
        args: list[Any] = [account_id]
        if order_id:
            sql += " AND order_id = ?"
            args.append(order_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [OrderOp.from_row(r) for r in self._db.execute(sql, args)]

    # ---- 改动 ----

    async def change_price(
        self,
        account_id: str,
        order_id: str,
        fees: dict[str, int],
        *,
        force: bool = False,
    ) -> tuple[int, int]:
        """改待付款订单的价格，返回 (改前总价, 改后总价)，单位分。

        `fees` 是 {价格项 key: 新金额（分）}，没传的项保持原值。平台要求每项都提交，
        这里先拉一次当前值补齐，再按总价检查保护线。
        """
        async with self._accounts.mtop(account_id) as mtop:
            api = OrderApi(mtop)
            fields = await api.price_fields(order_id)
            if not fields:
                raise ValueError("这个订单现在不能改价，只有待付款的订单可以")
            unknown = set(fees) - {f.key for f in fields}
            if unknown:
                raise ValueError(f"没有这些价格项：{', '.join(sorted(unknown))}")
            new = _fill(fields, fees)
            old_total = sum(f.price_cent or 0 for f in fields)
            new_total = sum(new.values())
            if new_total <= 0:
                raise ValueError("订单总价必须大于 0")
            if not force:
                check_price_change(old_total, new_total, self._max_change)
            change = {
                "from": old_total,
                "to": new_total,
                "fields": [
                    {"key": f.key, "name": f.name, "from": f.price_cent, "to": new[f.key]}
                    for f in fields
                ],
            }
            await self._record(
                account_id, order_id, "price", change, api.adjust_price(order_id, new)
            )
        with self._db:
            self._db.execute(
                "UPDATE orders SET price_cent = ? WHERE account_id = ? AND order_id = ?",
                (new_total, account_id, order_id),
            )
        return old_total, new_total

    # ---- 内部 ----

    async def _refresh_open(self, account_id: str, api: OrderApi) -> OrderSyncResult:
        """逐个拉本地还没结束（不是交易成功、已关闭）的订单的详情。单笔报错不影响别的。"""
        result = OrderSyncResult(source="detail")
        open_ids = [o.order_id for o in self.list(account_id) if o.status not in (DONE, CLOSED)]
        for n, order_id in enumerate(open_ids):
            if n:
                await asyncio.sleep(self._interval)
            try:
                detail = await api.detail(order_id)
            except MtopError:
                result.failed.append(order_id)
                continue
            with self._db:
                before = self._save_detail(account_id, detail, time.time())
            result.updated.append(order_id)
            if detail.status not in (OTHER, before):
                result.changed.append(order_id)
        return result

    def _save_detail(self, account_id: str, detail: OrderDetail, now: float) -> str | None:
        """详情写进本地库（没有就新建），返回写之前的状态，原来没有这笔订单时返回 None。

        详情页的状态标题认不出来时，不覆盖原来的状态。
        """
        row = self._db.execute(
            "SELECT status FROM orders WHERE account_id = ? AND order_id = ?",
            (account_id, detail.order_id),
        ).fetchone()
        self._db.execute(
            """
            INSERT INTO orders (account_id, order_id, item_id, title, pic_url, status, status_text,
                                price_cent, quantity, buyer_id, buyer_nick, created_at, raw,
                                first_seen_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, order_id) DO UPDATE SET
                item_id = COALESCE(NULLIF(excluded.item_id, ''), orders.item_id),
                title = COALESCE(NULLIF(excluded.title, ''), orders.title),
                pic_url = COALESCE(NULLIF(excluded.pic_url, ''), orders.pic_url),
                status = CASE WHEN excluded.status = ? THEN orders.status
                              ELSE excluded.status END,
                status_text = CASE WHEN excluded.status = ? THEN orders.status_text
                                   ELSE excluded.status_text END,
                price_cent = COALESCE(excluded.price_cent, orders.price_cent),
                quantity = excluded.quantity,
                buyer_id = COALESCE(NULLIF(excluded.buyer_id, ''), orders.buyer_id),
                buyer_nick = COALESCE(NULLIF(excluded.buyer_nick, ''), orders.buyer_nick),
                created_at = COALESCE(excluded.created_at, orders.created_at),
                raw = excluded.raw,
                synced_at = excluded.synced_at
            """,
            (
                account_id,
                detail.order_id,
                detail.item_id,
                detail.title,
                detail.pic_url,
                detail.status,
                detail.status_title,
                detail.amount_cent,
                detail.quantity,
                detail.buyer_id,
                detail.buyer_nick,
                detail.created_at,
                json.dumps(detail.raw, ensure_ascii=False),
                now,
                now,
                OTHER,  # 上面两处 CASE 用
                OTHER,
            ),
        )
        return row["status"] if row else None

    async def _record(
        self,
        account_id: str,
        order_id: str,
        action: str,
        detail: dict[str, Any],
        op: Awaitable[T],
    ) -> T:
        try:
            result = await op
        except Exception as exc:
            self._log_op(account_id, order_id, action, detail, error=str(exc) or type(exc).__name__)
            raise
        self._log_op(account_id, order_id, action, detail, error=None)
        return result

    def _log_op(
        self,
        account_id: str,
        order_id: str,
        action: str,
        detail: dict[str, Any],
        *,
        error: str | None,
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO order_ops"
                " (account_id, order_id, action, detail, ok, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    order_id,
                    action,
                    json.dumps(detail, ensure_ascii=False),
                    error is None,
                    error,
                    time.time(),
                ),
            )

    def _upsert(self, account_id: str, order: SoldOrder, now: float) -> None:
        self._db.execute(
            """
            INSERT INTO orders (account_id, order_id, item_id, title, pic_url, status, status_text,
                                price_cent, quantity, buyer_id, buyer_nick, created_at, raw,
                                first_seen_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, order_id) DO UPDATE SET
                item_id = excluded.item_id,
                title = COALESCE(NULLIF(excluded.title, ''), orders.title),
                pic_url = COALESCE(NULLIF(excluded.pic_url, ''), orders.pic_url),
                status = excluded.status,
                status_text = excluded.status_text,
                price_cent = COALESCE(excluded.price_cent, orders.price_cent),
                quantity = excluded.quantity,
                buyer_id = excluded.buyer_id,
                buyer_nick = excluded.buyer_nick,
                created_at = COALESCE(excluded.created_at, orders.created_at),
                raw = excluded.raw,
                synced_at = excluded.synced_at
            """,
            (
                account_id,
                order.order_id,
                order.item_id,
                order.title,
                order.pic_url,
                order.status,
                order.status_text,
                order.price_cent,
                order.quantity,
                order.buyer_id,
                order.buyer_nick,
                order.created_at,
                json.dumps(order.raw, ensure_ascii=False),
                now,
                now,
            ),
        )


def _fill(fields: list[PriceField], fees: dict[str, int]) -> dict[str, int]:
    """没改的价格项用当前值补齐；当前值也没有的，必须由调用方给。"""
    new: dict[str, int] = {}
    for f in fields:
        value = fees.get(f.key, f.price_cent)
        if value is None:
            raise ValueError(f"「{f.name}」没有当前值，请一起填上")
        if value < 0:
            raise ValueError(f"「{f.name}」不能是负数")
        new[f.key] = value
    return new
