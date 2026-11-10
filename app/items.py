"""商品管理：同步到本地库、查询、改价、下架、删除。

改描述、图片、邮费这些走编辑页，见 app/drafts.py。

每次改动都记进 item_ops，失败也记，方便事后查"系统到底动过什么"。

后台不调买家看的商品详情接口（pc.detail）：从后台调它会被闲鱼要求过滑块，一触发整个账号熔断
30 分钟。2026-11-10 用户反馈自己上架的商品点开详情就触发风控，之前只在看别人的商品时这样
（docs/protocol-notes.md#看别人的商品详情）。所以：
- 看商品的描述、图片、库存（`fetch_detail`）用卖家自己的编辑页接口 editDetail；
- 浏览数、收藏数只有 pc.detail 里有，由浏览器插件在用户自己的 Chrome 里拿到、页面转交过来存
  （`save_stats`），同步时不再逐个拉。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.accounts import Accounts
from app.db import Database, Row
from xianyu_core.item import ItemApi, ItemCard, ItemForm, cent_to_yuan

T = TypeVar("T")

ONLINE = 0

# pc.detail 的 itemDO 里的想要、浏览、收藏数，顺序和 save_stats 里一致
STAT_KEYS = ("wantCnt", "browseCnt", "collectCnt")
MAX_COUNT = 2**31 - 1  # 库里是 integer


class PriceGuardError(ValueError):
    """改价幅度超过保护线。确认没输错的话加 force=True。"""


def check_price_change(old: int, new: int, max_change: float) -> None:
    """`max_change` 是 0.5 时，降价超过一半或涨价超过一倍抛 PriceGuardError。订单改价也用。"""
    if old <= 0:
        return
    low, high = old * (1 - max_change), old / (1 - max_change)
    if not low <= new <= high:
        raise PriceGuardError(
            f"价格从 {cent_to_yuan(old)} 元改到 {cent_to_yuan(new)} 元，变化超过保护线"
        )


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
    def from_row(cls, row: Row) -> ItemRecord:
        return cls(**row)


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
    def from_row(cls, row: Row) -> ItemOp:
        return cls(**{**row, "detail": json.loads(row["detail"])})


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # 上次还在、这次列表里没有了（下架或删除）


class ItemService:
    """
    `interval`：连续调平台接口之间停几秒（翻页）。
    `max_change`：改价保护线。0.5 表示降价超过一半、或涨价超过一倍时要 force。
    """

    def __init__(
        self,
        db: Database,
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

    async def sync(self, account_id: str) -> SyncResult:
        """把个人主页上的商品全部拉下来写进本地库。

        只翻列表。浏览数、收藏数列表里没有，本地存过的（`save_stats`）留着不动。
        """
        async with self._accounts.mtop(account_id) as mtop:
            api = ItemApi(mtop)
            cards = [c async for c in api.iter_items(account_id, interval=self._interval)]

        now = time.time()
        known = {
            row["item_id"]: row["on_platform"]
            for row in self._db.execute(
                "SELECT item_id, on_platform FROM items WHERE account_id = %s", (account_id,)
            )
        }
        result = SyncResult()
        with self._db.transaction():
            for card in cards:
                self._upsert(account_id, card, now)
                (result.updated if card.item_id in known else result.added).append(card.item_id)
            seen = {card.item_id for card in cards}
            result.gone = [i for i, on in known.items() if on and i not in seen]
            self._db.executemany(
                "UPDATE items SET on_platform = false WHERE account_id = %s AND item_id = %s",
                [(account_id, item_id) for item_id in result.gone],
            )
        return result

    def list(
        self, account_id: str, *, status: int | None = None, include_gone: bool = False
    ) -> list[ItemRecord]:
        sql = "SELECT * FROM items WHERE account_id = %s"
        args: list[Any] = [account_id]
        if status is not None:
            sql += " AND status = %s"
            args.append(status)
        if not include_gone:
            sql += " AND on_platform"
        sql += " ORDER BY status, first_seen_at DESC"
        return [ItemRecord.from_row(r) for r in self._db.execute(sql, args)]

    def get(self, account_id: str, item_id: str) -> ItemRecord | None:
        row = self._db.execute(
            "SELECT * FROM items WHERE account_id = %s AND item_id = %s", (account_id, item_id)
        ).fetchone()
        return ItemRecord.from_row(row) if row else None

    async def fetch_detail(self, account_id: str, item_id: str) -> ItemForm:
        """从平台拉一次这个商品现在的描述、图片、价格、库存，顺手更新本地库里的价格。

        调的是卖家自己的编辑页接口（editDetail），不是买家看的商品详情（pc.detail，见文件开头）。
        多规格商品表单里的价格不一定是主页上显示的那个，不拿它改本地的。
        """
        async with self._accounts.mtop(account_id) as mtop:
            form = ItemForm.parse(await ItemApi(mtop).edit_detail(item_id))
        if form.price_cent and not form.multi_sku:
            with self._db.transaction():
                self._db.execute(
                    "UPDATE items SET price_cent = %s WHERE account_id = %s AND item_id = %s",
                    (form.price_cent, account_id, item_id),
                )
        return form

    def save_stats(self, account_id: str, item_id: str, item: dict[str, Any]) -> ItemRecord:
        """存浏览器插件从商品页拿到的想要、浏览、收藏数，不请求闲鱼。返回更新后的本地记录。

        `item` 是 pc.detail 返回的 itemDO（插件挑过字段，见 extension/goofish.js）。只取三个数，
        里面没有的那个留着本地原来的。格式不对、本地没有这个商品抛 ValueError。
        """
        if not isinstance(item, dict) or str(item.get("itemId", "")) != item_id:
            raise ValueError("详情里的商品 ID 和要存的对不上")
        want, browse, collect = (_count(item.get(key)) for key in STAT_KEYS)
        if want is None and browse is None and collect is None:
            raise ValueError("详情里没有想要、浏览、收藏数")
        with self._db.transaction():
            row = self._db.execute(
                "UPDATE items SET want_count = COALESCE(%s, want_count),"
                " browse_count = COALESCE(%s, browse_count),"
                " collect_count = COALESCE(%s, collect_count)"
                " WHERE account_id = %s AND item_id = %s RETURNING *",
                (want, browse, collect, account_id, item_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"本地没有商品 {item_id}，先同步一次")
        return ItemRecord.from_row(row)

    def history(self, account_id: str, item_id: str | None = None, limit: int = 50) -> list[ItemOp]:
        sql = "SELECT * FROM item_ops WHERE account_id = %s"
        args: list[Any] = [account_id]
        if item_id:
            sql += " AND item_id = %s"
            args.append(item_id)
        sql += " ORDER BY id DESC LIMIT %s"
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
                self.check_price(old, price_cent)
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
        with self._db.transaction():
            self._db.execute(
                "UPDATE items SET price_cent = %s WHERE account_id = %s AND item_id = %s",
                (price_cent, account_id, item_id),
            )
        return old

    async def downshelf(self, account_id: str, item_id: str) -> None:
        async with self._accounts.mtop(account_id) as mtop:
            await self._record(
                account_id, item_id, "downshelf", {}, ItemApi(mtop).downshelf(item_id)
            )
        with self._db.transaction():
            self._db.execute(
                "UPDATE items SET on_platform = false WHERE account_id = %s AND item_id = %s",
                (account_id, item_id),
            )

    async def delete(self, account_id: str, item_id: str) -> None:
        """删除商品，平台上不可恢复。本地记录一并删掉，改动记录保留。"""
        async with self._accounts.mtop(account_id) as mtop:
            await self._record(account_id, item_id, "delete", {}, ItemApi(mtop).delete(item_id))
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM items WHERE account_id = %s AND item_id = %s", (account_id, item_id)
            )

    def check_price(self, old: int, new: int) -> None:
        """改价幅度超过保护线时抛 PriceGuardError。编辑页提交时也用。"""
        check_price_change(old, new, self._max_change)

    def update_listing(
        self, account_id: str, item_id: str, *, title: str, price_cent: int, pic_url: str
    ) -> None:
        """编辑页提交以后先改本地库里的标题、价格、首图，下次同步时以平台上的为准。"""
        with self._db.transaction():
            self._db.execute(
                "UPDATE items SET title = %s, price_cent = %s, pic_url = %s"
                " WHERE account_id = %s AND item_id = %s",
                (title, price_cent, pic_url, account_id, item_id),
            )

    def add_published(self, account_id: str, card: ItemCard) -> None:
        """刚发布的商品先记进本地库，下次同步时以平台上的为准。"""
        with self._db.transaction():
            self._upsert(account_id, card, time.time())

    def log_op(
        self,
        account_id: str,
        item_id: str,
        action: str,
        detail: dict[str, Any],
        *,
        error: str | None,
    ) -> None:
        """记一次改动，`error` 为 None 表示成功。"""
        with self._db.transaction():
            self._db.execute(
                "INSERT INTO item_ops (account_id, item_id, action, detail, ok, error, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
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

    # ---- 内部 ----

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
            self.log_op(account_id, item_id, action, detail, error=str(exc) or type(exc).__name__)
            raise
        self.log_op(account_id, item_id, action, detail, error=None)
        return result

    def _upsert(self, account_id: str, card: ItemCard, now: float) -> None:
        """列表里的一条写进本地库。浏览数、收藏数列表里没有，已经存过的不动。"""
        self._db.execute(
            """
            INSERT INTO items (account_id, item_id, title, price_cent, status, category_id,
                               pic_url, want_count, on_platform, first_seen_at, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)
            ON CONFLICT (account_id, item_id) DO UPDATE SET
                title = excluded.title,
                price_cent = excluded.price_cent,
                status = excluded.status,
                category_id = excluded.category_id,
                pic_url = excluded.pic_url,
                want_count = excluded.want_count,
                on_platform = true,
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
                card.want_count,
                now,
                now,
            ),
        )


def _count(value: Any) -> int | None:
    """插件发来的一个计数：非负整数（或者写成字符串的）才认，别的当没有。"""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if 0 <= number <= MAX_COUNT else None
