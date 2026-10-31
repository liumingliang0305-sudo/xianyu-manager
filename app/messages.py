"""消息：即时消息进本地库，管理后台实时看到。

- `MessageStore`：会话和消息的本地库；
- `MessageHub`：`xym serve` 里每个账号一条即时消息长连接（xianyu_core/im.py 的 ImClient）。
  新消息一推下来就存库，再通知订阅的页面；停下了（登录失效、风控、被踢太多次）等条件变了再连。

补拉：每次连上先拉会话列表，最近的 BACKFILL_CONVERSATIONS 个会话里本地没跟上的，补拉最新的消息，
一直拉到和本地接上；别的会话等页面上点开时再拉。更早的消息在页面上往前翻时再拉。

发消息、标已读还没有做。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import websockets

from app.accounts import Accounts
from app.login import same_login
from xianyu_core.credential import Credential
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    ImRejectedError,
    RiskControlError,
)
from xianyu_core.im import (
    NEWEST_CURSOR,
    AccessToken,
    ChatMessage,
    Conversation,
    ImClient,
    ImConnection,
    ImEvent,
    ImStatus,
    fetch_access_token,
)

log = logging.getLogger(__name__)

CONVERSATION_PAGE = 50  # 会话列表每页几个，实测 50 可以
MAX_CONVERSATION_PAGES = 10
MESSAGE_PAGE = 20  # 历史消息每页几条，和网页版一样
MAX_NEWEST_PAGES = 5  # 补拉最新消息时最多往前翻几页去和本地接上
BACKFILL_CONVERSATIONS = 20  # 每次连上最多补拉几个会话
REQUEST_INTERVAL = 0.5  # 补拉时连续请求之间停几秒
WATCH_INTERVAL = 30.0  # 隔多久看一次有没有要连的账号
RETRY_AFTER = 60.0  # 意外停下的，或者风控解除了的，至少隔这么久再连
SUBSCRIBER_QUEUE = 100
CLOSING = {"type": "closing"}  # 服务要退出了，订阅到此结束

# 会话列表里没给对方昵称的，用对方最近一条文本消息的提醒标题补上（交易卡片之类的提醒标题不是昵称）
_CONVERSATION_SELECT = """
SELECT c.account_id, c.cid, c.peer_id,
       COALESCE(NULLIF(c.peer_name, ''), (
           SELECT sender_name FROM messages
           WHERE account_id = c.account_id AND cid = c.cid AND sender_id = c.peer_id
                 AND content_type = 1 AND sender_name != ''
           ORDER BY sent_at DESC LIMIT 1
       ), '') AS peer_name,
       c.biz_type, c.item_id, c.item_title,
       c.item_pic, c.order_id, c.unread, c.updated_at, c.fetched_at, c.older_more,
       c.first_seen_at,
       COALESCE(m.text, '') AS last_text, COALESCE(m.sender_id, '') AS last_sender_id,
       m.sent_at AS last_sent_at
FROM conversations c
LEFT JOIN messages m ON m.rowid = (
    SELECT rowid FROM messages
    WHERE account_id = c.account_id AND cid = c.cid
    ORDER BY sent_at DESC, message_id DESC LIMIT 1
)
"""


class ImOfflineError(RuntimeError):
    """即时消息没连上，没法向平台拉消息。"""


@dataclass
class ConversationRecord:
    account_id: str
    cid: str
    peer_id: str
    peer_name: str
    biz_type: str
    item_id: str
    item_title: str
    item_pic: str
    order_id: str
    unread: int
    updated_at: float | None
    fetched_at: float | None
    older_more: bool | None  # 平台上还有没有更早的消息，没往前翻过是 None
    first_seen_at: float
    last_text: str
    last_sender_id: str
    last_sent_at: float | None
    stale: bool  # 平台上有本地还没拉的消息，点开时要补拉

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ConversationRecord:
        data = dict(row)
        older_more = data["older_more"]
        fetched_at = data["fetched_at"]
        return cls(
            **{
                **data,
                "older_more": None if older_more is None else bool(older_more),
                "stale": fetched_at is None or (data["updated_at"] or 0) > fetched_at,
            }
        )


@dataclass
class MessageRecord:
    account_id: str
    message_id: str
    cid: str
    sender_id: str
    sender_name: str
    sent_at: float | None
    content_type: int | None
    text: str
    item_id: str
    received_at: float
    mine: bool  # 是不是这个账号自己发的

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> MessageRecord:
        return cls(**dict(row), mine=row["sender_id"] == row["account_id"])


class MessageStore:
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    # ---- 查询 ----

    def conversations(self, account_id: str) -> list[ConversationRecord]:
        """最近有动静的在前。"""
        rows = self._db.execute(
            _CONVERSATION_SELECT + " WHERE c.account_id = ?"
            " ORDER BY COALESCE(c.updated_at, c.first_seen_at) DESC, c.cid",
            (account_id,),
        )
        return [ConversationRecord.from_row(r) for r in rows]

    def conversation(self, account_id: str, cid: str) -> ConversationRecord | None:
        row = self._db.execute(
            _CONVERSATION_SELECT + " WHERE c.account_id = ? AND c.cid = ?", (account_id, cid)
        ).fetchone()
        return ConversationRecord.from_row(row) if row else None

    def messages(
        self, account_id: str, cid: str, *, limit: int = 50
    ) -> tuple[list[MessageRecord], bool]:
        """一个会话最新的 `limit` 条消息（按时间正序），和本地还有没有更早的。"""
        rows = self._db.execute(
            "SELECT account_id, message_id, cid, sender_id, sender_name, sent_at, content_type,"
            " text, item_id, received_at FROM messages WHERE account_id = ? AND cid = ?"
            " ORDER BY sent_at DESC, message_id DESC LIMIT ?",
            (account_id, cid, limit + 1),
        ).fetchall()
        records = [MessageRecord.from_row(r) for r in rows[:limit]]
        return records[::-1], len(rows) > limit

    def newest_update(self, account_id: str) -> float | None:
        row = self._db.execute(
            "SELECT MAX(updated_at) FROM conversations WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row[0]

    def stale(self, account_id: str, cids: list[str]) -> list[str]:
        """`cids` 里本地没跟上平台的会话，保持传进来的顺序。"""
        stale = {
            row["cid"]
            for row in self._db.execute(
                "SELECT cid FROM conversations WHERE account_id = ?"
                " AND (fetched_at IS NULL OR COALESCE(updated_at, 0) > fetched_at)",
                (account_id,),
            )
        }
        return [cid for cid in cids if cid in stale]

    def older(self, account_id: str, cid: str) -> tuple[int | None, bool | None]:
        """往前翻历史消息的 (游标, 平台上还有没有更早的)。"""
        row = self._db.execute(
            "SELECT older_cursor, older_more FROM conversations WHERE account_id = ? AND cid = ?",
            (account_id, cid),
        ).fetchone()
        if row is None or row["older_more"] is None:
            return None, None
        return row["older_cursor"], bool(row["older_more"])

    def oldest_sent_at(self, account_id: str, cid: str) -> float | None:
        row = self._db.execute(
            "SELECT MIN(sent_at) FROM messages WHERE account_id = ? AND cid = ?",
            (account_id, cid),
        ).fetchone()
        return row[0]

    # ---- 写入 ----

    def save_conversations(
        self, account_id: str, conversations: list[Conversation], now: float | None = None
    ) -> None:
        """会话列表写进本地库。未读数以平台为准；补拉进度（fetched_at、older_*）不动。"""
        now = time.time() if now is None else now
        with self._db:
            for conv in conversations:
                self._db.execute(
                    """
                    INSERT INTO conversations (account_id, cid, peer_id, peer_name, biz_type,
                                               item_id, item_title, item_pic, order_id, unread,
                                               updated_at, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (account_id, cid) DO UPDATE SET
                        peer_id = COALESCE(NULLIF(excluded.peer_id, ''), conversations.peer_id),
                        peer_name = COALESCE(NULLIF(excluded.peer_name, ''),
                                             conversations.peer_name),
                        biz_type = COALESCE(NULLIF(excluded.biz_type, ''), conversations.biz_type),
                        item_id = COALESCE(NULLIF(excluded.item_id, ''), conversations.item_id),
                        item_title = COALESCE(NULLIF(excluded.item_title, ''),
                                              conversations.item_title),
                        item_pic = COALESCE(NULLIF(excluded.item_pic, ''), conversations.item_pic),
                        order_id = COALESCE(NULLIF(excluded.order_id, ''), conversations.order_id),
                        unread = excluded.unread,
                        updated_at = CASE
                            WHEN conversations.updated_at IS NULL
                                 OR excluded.updated_at > conversations.updated_at
                            THEN excluded.updated_at ELSE conversations.updated_at END
                    """,
                    (
                        account_id,
                        conv.cid,
                        conv.peer_id,
                        conv.peer_name,
                        conv.biz_type,
                        conv.item_id,
                        conv.item_title,
                        conv.item_pic,
                        conv.order_id,
                        conv.unread,
                        conv.updated_at,
                        now,
                    ),
                )

    def save_messages(
        self, account_id: str, messages: list[ChatMessage], now: float | None = None
    ) -> list[ChatMessage]:
        """存消息，已经有的跳过。返回新存的。"""
        now = time.time() if now is None else now
        with self._db:
            return [m for m in messages if self._insert(account_id, m, now)]

    def add_live(self, account_id: str, message: ChatMessage, now: float | None = None) -> bool:
        """存一条实时推下来的消息，更新会话的时间和未读数。本地没有这个会话就先建一个。

        补拉进度原来跟上了的，收到推送以后仍然算跟上（连着的时候推送不会漏）。
        """
        now = time.time() if now is None else now
        mine = message.sender_id == account_id
        named = not mine and message.content_type == 1  # 只有文本消息的提醒标题是昵称
        at = message.sent_at or now
        with self._db:
            if not self._insert(account_id, message, now):
                return False
            self._db.execute(
                "INSERT OR IGNORE INTO conversations"
                " (account_id, cid, peer_id, peer_name, biz_type, item_id, updated_at,"
                "  first_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    message.cid,
                    "" if mine else message.sender_id,
                    message.sender_name if named else "",
                    "1" if message.item_id else "",
                    message.item_id,
                    at,
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE conversations SET
                    fetched_at = CASE WHEN fetched_at >= COALESCE(updated_at, 0)
                                      THEN MAX(fetched_at, ?) ELSE fetched_at END,
                    updated_at = MAX(COALESCE(updated_at, 0), ?),
                    unread = CASE WHEN ? THEN 0 ELSE unread + 1 END
                WHERE account_id = ? AND cid = ?
                """,
                (at, at, mine, account_id, message.cid),
            )
        return True

    def mark_fetched(self, account_id: str, cid: str, now: float | None = None) -> None:
        """最新消息补拉完了：本地跟上了平台。"""
        now = time.time() if now is None else now
        with self._db:
            self._db.execute(
                "UPDATE conversations SET fetched_at = COALESCE(updated_at, ?)"
                " WHERE account_id = ? AND cid = ?",
                (now, account_id, cid),
            )

    def set_older(self, account_id: str, cid: str, cursor: int | None, more: bool) -> None:
        with self._db:
            self._db.execute(
                "UPDATE conversations SET older_cursor = ?, older_more = ?"
                " WHERE account_id = ? AND cid = ?",
                (cursor, more, account_id, cid),
            )

    def _insert(self, account_id: str, m: ChatMessage, now: float) -> bool:
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO messages (account_id, message_id, cid, sender_id, sender_name,"
            " sent_at, content_type, text, item_id, raw, received_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                m.message_id,
                m.cid,
                m.sender_id,
                m.sender_name,
                m.sent_at,
                m.content_type,
                m.text,
                m.item_id,
                json.dumps(m.raw, ensure_ascii=False, default=str),
                now,
            ),
        )
        return cursor.rowcount > 0


# ---------- 长期在线 ----------


@dataclass
class _Line:
    """一个账号的即时消息。"""

    client: ImClient
    task: asyncio.Task[None] | None = None
    error: Exception | None = None  # 为什么停下的
    ended_at: float | None = None
    credential: Credential | None = None  # 停下时的凭证，换了登录就再连


def _needs(error: Exception | None) -> str | None:
    """停下以后要人做什么：login 重新登录；unblock 完成验证后解除熔断；reconnect 手动重新连接。"""
    if isinstance(error, AuthRequiredError):
        return "login"
    if isinstance(error, RiskControlError | CircuitOpenError):
        return "unblock"
    if isinstance(error, ImRejectedError):
        return "reconnect"
    return None


class MessageHub:
    """`xym serve` 里所有账号的即时消息。

    `start()` 以后每隔 `watch_interval` 秒看一遍账号：没连的连上；停下了的，条件变了再连——
    换了登录（重新扫码、导入、免密续期）、熔断解除了、意外停下的过了 RETRY_AFTER。
    被踢太多次、连接数超限的只等换登录或者手动重新连接，免得和网页版抢连接。

    取 accessToken 走账号的锁（`locks`），和别的平台请求串行。
    """

    def __init__(
        self,
        store: MessageStore,
        accounts: Accounts,
        locks: Mapping[str, asyncio.Lock],
        *,
        connect: Callable[..., Awaitable[Any]] = websockets.connect,
        client_options: dict[str, Any] | None = None,
        watch_interval: float = WATCH_INTERVAL,
        request_interval: float = REQUEST_INTERVAL,
    ):
        self.store = store
        self._accounts = accounts
        self._locks = locks
        self._connect = connect
        self._client_options = client_options or {}
        self._watch_interval = watch_interval
        self._interval = request_interval
        self._lines: dict[str, _Line] = {}
        self._subscribers: defaultdict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._fetch_locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._watcher: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    @property
    def running(self) -> bool:
        return self._watcher is not None

    async def start(self) -> None:
        self._watcher = asyncio.create_task(self._watch())

    async def aclose(self) -> None:
        tasks = [line.task for line in self._lines.values() if line.task]
        if self._watcher:
            tasks.append(self._watcher)
            self._watcher = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 状态和订阅 ----

    def status(self, account_id: str) -> dict[str, Any]:
        """state：connecting / online / waiting（等着重连）/ stopped / off（没开即时消息）。"""
        line = self._lines.get(account_id)
        if not self.running:
            return {
                "state": "off",
                "since": None,
                "message": "即时消息只在 xym serve 里连",
                "needs": None,
            }
        if line is None:
            return {"state": "stopped", "since": None, "message": "还没连", "needs": None}
        status: ImStatus = line.client.status
        if status.state == "stopped" and line.task and not line.task.done():
            # 刚开的还没跑起来（或者在等旧连接关掉）
            status = ImStatus("connecting", status.since)
        return {
            "state": status.state,
            "since": status.since,
            "message": status.message,
            "needs": _needs(line.error) if status.state == "stopped" else None,
        }

    @contextlib.contextmanager
    def subscribe(self, account_id: str) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        """订阅这个账号的变化：{"type": "message"|"messages"|"conversations"|"status", ...}。

        收到以后重新读本地库就行。订阅的人读得太慢时多出来的事件直接丢掉。
        收到 CLOSING 就该结束了。
        """
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(SUBSCRIBER_QUEUE)
        if self._closing:
            queue.put_nowait(CLOSING)
        self._subscribers[account_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[account_id].discard(queue)

    def close_streams(self) -> None:
        """服务要退出了，让所有订阅结束。可以在信号处理函数、别的线程里调。"""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._close_streams)

    def _close_streams(self) -> None:
        self._closing = True
        for queues in self._subscribers.values():
            for queue in queues:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(CLOSING)

    def _publish(self, account_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(account_id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def _publish_status(self, account_id: str) -> None:
        self._publish(account_id, {"type": "status", **self.status(account_id)})

    # ---- 连接 ----

    def restart(self, account_id: str) -> None:
        """手动重新连接：断开现有的（如果有），马上重连。"""
        self._require_running()
        if account_id not in self._accounts.ids():
            raise ValueError(f"找不到账号 {account_id}")
        self._start(account_id)

    def resume(self, account_id: str) -> None:
        """登录、解除熔断以后调：这个账号没在连的话马上连。"""
        if not self.running:
            return
        line = self._lines.get(account_id)
        if line is None or (line.task is not None and line.task.done()):
            self._start(account_id)

    async def _watch(self) -> None:
        while True:
            try:
                for account_id in self._accounts.ids():
                    line = self._lines.get(account_id)
                    if line is None or self._may_restart(account_id, line):
                        self._start(account_id)
            except Exception:
                log.exception("检查即时消息连接出错")
            await asyncio.sleep(self._watch_interval)

    def _may_restart(self, account_id: str, line: _Line) -> bool:
        if line.task is None or not line.task.done() or line.ended_at is None:
            return False
        waited = time.time() - line.ended_at
        try:
            current = self._accounts.load(account_id)
        except (OSError, ValueError):
            return False
        if line.credential and not same_login(current, line.credential):
            return True
        if isinstance(line.error, AuthRequiredError | ImRejectedError):
            return False
        if isinstance(line.error, RiskControlError | CircuitOpenError):
            guard = self._accounts.guard
            return guard is not None and guard.block(account_id) is None and waited >= RETRY_AFTER
        return waited >= RETRY_AFTER

    def _start(self, account_id: str) -> None:
        """开一条新的；原来那条还在跑就先停掉，等它关了新的再连。"""
        old = self._lines.get(account_id)
        previous = old.task if old and old.task and not old.task.done() else None
        if previous:
            previous.cancel()

        def changed(_status: ImStatus) -> None:
            if self._lines.get(account_id) is line:
                self._publish_status(account_id)

        line = _Line(client=self._client(account_id, changed))
        self._lines[account_id] = line
        line.task = asyncio.create_task(self._run(account_id, line, previous))

    def _client(self, account_id: str, on_status: Callable[[ImStatus], None]) -> ImClient:
        device_id = self._accounts.device_id(account_id)

        async def fetch_token() -> AccessToken:
            async with self._locks[account_id]:
                async with self._accounts.mtop(account_id) as mtop:
                    return await fetch_access_token(mtop, device_id)

        return ImClient(
            device_id=device_id,
            fetch_token=fetch_token,
            load_cookies=lambda: self._accounts.load(account_id).cookies,
            connect=self._connect,
            on_status=on_status,
            **self._client_options,
        )

    async def _run(self, account_id: str, line: _Line, previous: asyncio.Task[None] | None) -> None:
        if previous:
            await asyncio.wait({previous})

        async def on_event(conn: ImConnection, event: ImEvent) -> None:
            await self._on_event(account_id, event)

        async def on_connected(conn: ImConnection) -> None:
            await self.backfill(account_id, conn)

        try:
            await line.client.run(on_event, on_connected)
        except Exception as exc:
            line.error = exc
            log.warning("账号 %s 的即时消息停了：%s", account_id, exc)
        finally:
            line.ended_at = time.time()
            with contextlib.suppress(OSError, ValueError):
                line.credential = self._accounts.load(account_id)
            if self._lines.get(account_id) is line:
                self._publish_status(account_id)

    async def _on_event(self, account_id: str, event: ImEvent) -> None:
        if isinstance(event, ChatMessage) and self.store.add_live(account_id, event):
            self._publish(
                account_id, {"type": "message", "cid": event.cid, "message_id": event.message_id}
            )

    def _require_running(self) -> None:
        if not self.running:
            raise ImOfflineError("即时消息没有开，只在 xym serve 里连")

    def _online(self, account_id: str) -> ImConnection:
        self._require_running()
        line = self._lines.get(account_id)
        conn = line.client.connection if line else None
        if conn is None or conn.closed:
            message = line.client.status.message if line else ""
            raise ImOfflineError(f"即时消息没连上{f'：{message}' if message else ''}")
        return conn

    # ---- 拉消息 ----

    async def backfill(self, account_id: str, conn: ImConnection) -> list[str]:
        """连上以后补拉：会话列表，和最近的会话里本地没跟上的消息。返回补拉了的会话。

        会话列表从最新的往后翻，翻到上次已经知道的时间点就停。
        """
        watermark = self.store.newest_update(account_id)
        listed: list[Conversation] = []
        cursor = NEWEST_CURSOR
        for n in range(MAX_CONVERSATION_PAGES):
            if n:
                await asyncio.sleep(self._interval)
            page = await conn.list_conversations(account_id, cursor, CONVERSATION_PAGE)
            listed += page.conversations
            oldest = page.conversations[-1].updated_at if page.conversations else None
            if not page.has_more or page.next_cursor is None:
                break
            if watermark is not None and oldest is not None and oldest <= watermark:
                break
            cursor = page.next_cursor
        self.store.save_conversations(account_id, listed)
        self._publish(account_id, {"type": "conversations"})

        stale = self.store.stale(account_id, [c.cid for c in listed[:BACKFILL_CONVERSATIONS]])
        for cid in stale:
            await asyncio.sleep(self._interval)
            await self.fetch_newest(account_id, cid, conn)
        return stale

    async def fetch(self, account_id: str, cid: str, *, older: bool = False) -> dict[str, Any]:
        """页面上用：拉一个会话最新的消息，或者往前翻一页。"""
        conn = self._online(account_id)
        if self.store.conversation(account_id, cid) is None:
            raise ValueError(f"本地没有会话 {cid}")
        if older:
            added = await self.fetch_older(account_id, cid, conn)
        else:
            added = await self.fetch_newest(account_id, cid, conn)
        return {"added": added, "older_more": self.store.older(account_id, cid)[1]}

    async def fetch_newest(self, account_id: str, cid: str, conn: ImConnection) -> int:
        """从最新的往前拉，拉到本地已经有的消息（接上了）为止，最多 MAX_NEWEST_PAGES 页；
        本地还没有这个会话的消息时只拉一页。

        头一次拉、或者没接上（中间断了一截）时，往前翻的游标从这里接着往前，这样翻历史时能把
        断掉的一截补上。返回新存了几条。
        """
        async with self._fetch_locks[account_id, cid]:
            _, older_more = self.store.older(account_id, cid)
            pages = MAX_NEWEST_PAGES if self.store.oldest_sent_at(account_id, cid) else 1
            cursor, added, joined = NEWEST_CURSOR, 0, False
            for n in range(pages):
                if n:
                    await asyncio.sleep(self._interval)
                page = await conn.list_messages(cid, cursor, MESSAGE_PAGE)
                new = self.store.save_messages(account_id, page.messages)
                added += len(new)
                joined = len(new) < len(page.messages)
                more = page.has_more and page.next_cursor is not None
                if joined or not more:
                    break
                cursor = page.next_cursor
            if older_more is None or not joined:
                self.store.set_older(account_id, cid, page.next_cursor if more else None, more)
            self.store.mark_fetched(account_id, cid)
        if added:
            self._publish(account_id, {"type": "messages", "cid": cid})
        return added

    async def fetch_older(self, account_id: str, cid: str, conn: ImConnection) -> int:
        """往前翻一页历史消息，返回新存了几条（翻到本地已有的那一截时可能是 0）。"""
        async with self._fetch_locks[account_id, cid]:
            cursor, more = self.store.older(account_id, cid)
            if more is False:
                return 0
            if cursor is None:
                oldest = self.store.oldest_sent_at(account_id, cid)
                cursor = round(oldest * 1000) if oldest else NEWEST_CURSOR
            page = await conn.list_messages(cid, cursor, MESSAGE_PAGE)
            added = len(self.store.save_messages(account_id, page.messages))
            more = page.has_more and page.next_cursor is not None
            self.store.set_older(account_id, cid, page.next_cursor if more else None, more)
        if added:
            self._publish(account_id, {"type": "messages", "cid": cid})
        return added
