"""消息：即时消息进本地库，管理后台实时看到。

- `MessageStore`：会话和消息的本地库；
- `MessageHub`：`xym serve` 里每个账号一条即时消息长连接（xianyu_core/im.py 的 ImClient）。
  新消息一推下来就存库，再通知订阅的页面；停下了（登录失效、风控、被踢太多次）等条件变了再连。
  一个账号同一时间只能有一个进程在连（`ImLock`）：两个进程用同一个设备 ID 连，会互相挤掉线。

补拉：每次连上先拉会话列表，最近的 BACKFILL_CONVERSATIONS 个会话里本地没跟上的，补拉最新的消息，
一直拉到和本地接上；别的会话等页面上点开时再拉。更早的消息在页面上往前翻时再拉。

只存和人的聊天：平台的活动推广、通知号、客服机器人之类的会话（见 xianyu_core/im.py 的
USER_SESSION_TYPES）列会话时跳过，推下来的消息也不存；以前存进来的，启动时删掉。

发消息：只能在商品会话里发文本，走同一条连接，过账号的限流（发消息每分钟 10 条）和熔断；
发出去的消息马上存库、通知页面。标已读还没有做。
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import httpx
import websockets

from app.accounts import Accounts
from app.login import same_login
from xianyu_core.credential import Credential
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    ImRejectedError,
    RiskControlError,
    XianyuError,
)
from xianyu_core.im import (
    NEWEST_CURSOR,
    PERSON_CONTENT_TYPES,
    PERSON_NOTICE,
    SEND_API,
    SYSTEM_USER_ID,
    USER_SESSION_TYPES,
    AccessToken,
    ChatMessage,
    Conversation,
    ImClient,
    ImConnection,
    ImEvent,
    ImStatus,
    fetch_access_token,
    fetch_peer_nick,
)

log = logging.getLogger(__name__)

CONVERSATION_PAGE = 50  # 会话列表每页几个，实测 50 可以
MAX_CONVERSATION_PAGES = 10
MESSAGE_PAGE = 20  # 历史消息每页几条，和网页版一样
MAX_NEWEST_PAGES = 5  # 补拉最新消息时最多往前翻几页去和本地接上
BACKFILL_CONVERSATIONS = 20  # 每次连上最多补拉几个会话
NAME_LOOKUPS = 20  # 每次连上最多向平台查几个对方昵称（别的等页面上点开时再查）
REQUEST_INTERVAL = 0.5  # 补拉时连续请求之间停几秒
WATCH_INTERVAL = 30.0  # 隔多久看一次有没有要连的账号
RETRY_AFTER = 60.0  # 意外停下的，或者风控解除了的，至少隔这么久再连
SUBSCRIBER_QUEUE = 100
CLOSING = {"type": "closing"}  # 服务要退出了，订阅到此结束
MAX_TEXT = 500  # 一条消息最多几个字。平台的上限没查到，这是自己定的，防止误贴一大段

# 不知道对方昵称的，用对方最近一条本人发的消息里的昵称补上（见 ChatMessage.sender_name）
_CONVERSATION_SELECT = """
SELECT c.account_id, c.cid, c.peer_id,
       COALESCE(NULLIF(c.peer_name, ''), (
           SELECT sender_name FROM messages
           WHERE account_id = c.account_id AND cid = c.cid AND sender_id = c.peer_id
                 AND sender_name != ''
           ORDER BY sent_at DESC LIMIT 1
       ), '') AS peer_name,
       c.peer_type, c.biz_type, c.item_id, c.item_title,
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


class ImBusyError(RuntimeError):
    """别的进程（另一个 xym serve）正在连这个账号的即时消息，这里不连。"""


class ImLock:
    """一个账号的即时消息只让一个进程连：`<账号目录>/<unb>.im.lock` 上的文件锁（flock）。

    进程退出（包括被杀掉）时系统自动放开，不会留下死锁。锁住以后文件里写上进程号，给没抢到的一边看。
    文件用完也不删：删了以后，别的进程可能锁在已经删掉的那个文件上，两边就都以为自己抢到了。
    """

    def __init__(self, path: Path):
        self.path = path
        self._file: IO[str] | None = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> bool:
        """抢锁，抢到（或者本来就是自己的）返回 True，别的进程拿着返回 False。不等。"""
        if self._file:
            return True
        file = self.path.open("a+", encoding="utf-8")  # 先不清空，抢到了再写
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            file.close()
            return False
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()))
        file.flush()
        self._file = file
        return True

    def holder(self) -> str:
        """拿着锁的进程号，读不到是空字符串。"""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def release(self) -> None:
        if self._file:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None


@dataclass
class ConversationRecord:
    account_id: str
    cid: str
    peer_id: str
    peer_name: str
    peer_type: str
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

    def message(self, account_id: str, message_id: str) -> MessageRecord | None:
        row = self._db.execute(
            "SELECT account_id, message_id, cid, sender_id, sender_name, sent_at, content_type,"
            " text, item_id, received_at FROM messages WHERE account_id = ? AND message_id = ?",
            (account_id, message_id),
        ).fetchone()
        return MessageRecord.from_row(row) if row else None

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

    def nameless(self, account_id: str, cids: list[str]) -> list[str]:
        """`cids` 里还不知道对方昵称、也没向平台查过的会话，保持传进来的顺序。

        对方本人发过消息的不算：昵称从消息里来。
        """
        rows = self._db.execute(
            """
            SELECT c.cid FROM conversations c
            WHERE c.account_id = ? AND c.peer_name = '' AND c.named_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.account_id = c.account_id AND m.cid = c.cid AND m.sender_id = c.peer_id
                        AND m.sender_name != '')
            """,
            (account_id,),
        )
        nameless = {row["cid"] for row in rows}
        return [cid for cid in cids if cid in nameless]

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
        """会话列表写进本地库。未读数以平台为准；补拉进度（fetched_at、older_*）不动。

        对方昵称：没向平台查过的，跟着会话列表走（最后一条是对方本人发的才有，不然是空的，
        显示时再从消息里找）；查过的，会话列表里有新的才换。
        """
        now = time.time() if now is None else now
        with self._db:
            for conv in conversations:
                self._db.execute(
                    """
                    INSERT INTO conversations (account_id, cid, peer_id, peer_name, peer_type,
                                               biz_type, item_id, item_title, item_pic, order_id,
                                               unread, updated_at, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (account_id, cid) DO UPDATE SET
                        peer_id = COALESCE(NULLIF(excluded.peer_id, ''), conversations.peer_id),
                        peer_name = CASE WHEN conversations.named_at IS NULL
                                         THEN excluded.peer_name
                                         ELSE COALESCE(NULLIF(excluded.peer_name, ''),
                                                       conversations.peer_name) END,
                        peer_type = COALESCE(NULLIF(excluded.peer_type, ''),
                                             conversations.peer_type),
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
                        conv.peer_type,
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
        named = not mine and message.sender_name != ""
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
                    message.session_type or ("1" if message.item_id else ""),
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

    def remove_non_user(self) -> int:
        """删掉不是和人聊天的会话（活动推广、通知号、客服机器人之类）和它们的消息，返回删了几个会话。

        会话类型还不知道的（推送先建出来的会话）留着，等会话列表补上类型。
        """
        types = sorted(USER_SESSION_TYPES)
        marks = ", ".join("?" * len(types))
        with self._db:
            rows = self._db.execute(
                "SELECT account_id, cid FROM conversations"
                f" WHERE (biz_type != '' AND biz_type NOT IN ({marks})) OR peer_id = ?",
                (*types, SYSTEM_USER_ID),
            ).fetchall()
            for row in rows:
                key = (row["account_id"], row["cid"])
                self._db.execute("DELETE FROM messages WHERE account_id = ? AND cid = ?", key)
                self._db.execute("DELETE FROM conversations WHERE account_id = ? AND cid = ?", key)
        return len(rows)

    def forget_unsure_names(self) -> int:
        """以前存的消息，sender_name 直接取的提醒标题，交易卡片、自动回复的标题不是昵称
        （"交易消息"、"你有一条新消息"），清掉。返回清了几条，清过一次以后是 0。"""
        types = sorted(PERSON_CONTENT_TYPES)
        marks = ", ".join("?" * len(types))
        with self._db:
            cursor = self._db.execute(
                f"""
                UPDATE messages SET sender_name = ''
                WHERE sender_name != '' AND (
                    content_type IS NULL OR content_type NOT IN ({marks})
                    OR COALESCE(
                        json_extract(raw, '$.message.extension.reminderNotice'),
                        json_extract(raw, '$."1"."10".reminderNotice'), '') != ?)
                """,
                (*types, PERSON_NOTICE),
            )
        return cursor.rowcount

    def set_peer_name(self, account_id: str, cid: str, name: str, now: float | None = None) -> None:
        """记下向平台查到的对方昵称。查不到（空字符串）也记下查过了，不再反复查。"""
        now = time.time() if now is None else now
        with self._db:
            self._db.execute(
                "UPDATE conversations SET peer_name = COALESCE(NULLIF(?, ''), peer_name),"
                " named_at = ? WHERE account_id = ? AND cid = ?",
                (name, now, account_id, cid),
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
    """停下以后要人做什么：login 重新登录；unblock 完成验证后解除熔断；reconnect 手动重新连接；
    elsewhere 别的 xym serve 在连，关掉它（这边会自动接上）。"""
    if isinstance(error, ImBusyError):
        return "elsewhere"
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

    连之前先抢这个账号的 `ImLock`，抢到了一直拿着，`aclose()` 时才放开；没抢到（另一个 xym serve
    在连）就不连，每隔 `watch_interval` 秒再抢一次，那边退出了这边自动接上。

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
        self._claims: dict[str, ImLock] = {}
        self._subscribers: defaultdict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._fetch_locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._send_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._name_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._background: set[asyncio.Task[Any]] = set()
        self._watcher: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    @property
    def running(self) -> bool:
        return self._watcher is not None

    async def start(self) -> None:
        if removed := self.store.remove_non_user():
            log.info("删掉了 %d 个不是和人聊天的会话（活动推广、通知号之类）", removed)
        if forgotten := self.store.forget_unsure_names():
            log.info("清掉了 %d 条消息里不是昵称的发送人名字", forgotten)
        self._watcher = asyncio.create_task(self._watch())

    async def aclose(self) -> None:
        tasks = [line.task for line in self._lines.values() if line.task]
        tasks += self._background
        if self._watcher:
            tasks.append(self._watcher)
            self._watcher = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for claim in self._claims.values():
            claim.release()

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
        busy = isinstance(line.error, ImBusyError) and status.state == "stopped"
        return {
            "state": status.state,
            "since": line.ended_at if busy else status.since,
            "message": str(line.error) if busy else status.message,
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
        if isinstance(line.error, ImBusyError):
            return self._claim(account_id)  # 只是抢一下文件锁，不发请求
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

    def _claim(self, account_id: str) -> bool:
        """抢这个账号的 ImLock，抢到（或者本来就拿着）返回 True。"""
        claim = self._claims.get(account_id)
        if claim is None:
            claim = ImLock(self._accounts.root / f"{account_id}.im.lock")
            self._claims[account_id] = claim
        return claim.acquire()

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
            if not self._claim(account_id):
                pid = self._claims[account_id].holder()
                raise ImBusyError(
                    f"另一个 xym serve{f'（进程 {pid}）' if pid else ''}正在连这个账号，"
                    "这里先不连，那边退出以后自动接上"
                )
            await line.client.run(on_event, on_connected)
        except ImBusyError as exc:
            line.error = exc
            log.warning("账号 %s：%s", account_id, exc)
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
        if not isinstance(event, ChatMessage):
            return
        if event.session_type and event.session_type not in USER_SESSION_TYPES:
            log.debug(
                "跳过会话类型 %s 的消息 %s（不是和人的聊天）", event.session_type, event.message_id
            )
            return
        if self.store.add_live(account_id, event):
            self._publish(
                account_id, {"type": "message", "cid": event.cid, "message_id": event.message_id}
            )
        else:
            log.debug("消息 %s 已经存过了（自己发的推回来、或者重复推送）", event.message_id)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        """在后台跑，不耽误收推送；aclose() 时取消。"""
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _require_running(self) -> None:
        if not self.running:
            raise ImOfflineError("即时消息没有开，只在 xym serve 里连")

    def _online(self, account_id: str) -> ImConnection:
        self._require_running()
        line = self._lines.get(account_id)
        conn = line.client.connection if line else None
        if conn is None or conn.closed:
            if line and isinstance(line.error, ImBusyError):
                message = str(line.error)
            else:
                message = line.client.status.message if line else ""
            raise ImOfflineError(f"即时消息没连上{f'：{message}' if message else ''}")
        return conn

    # ---- 拉消息 ----

    async def backfill(self, account_id: str, conn: ImConnection) -> list[str]:
        """连上以后补拉：会话列表，和最近的会话里本地没跟上的消息。返回补拉了的会话。

        会话列表整个翻一遍（最多 MAX_CONVERSATION_PAGES 页，一页一个即时消息请求），顺便更新
        每个会话的未读数和对方昵称。最近的会话里还不知道昵称的，在后台向平台查（见 lookup_names）。
        """
        listed: dict[str, Conversation] = {}
        cursor = NEWEST_CURSOR
        for n in range(MAX_CONVERSATION_PAGES):
            if n:
                await asyncio.sleep(self._interval)
            page = await conn.list_conversations(account_id, cursor, CONVERSATION_PAGE)
            listed.update((c.cid, c) for c in page.conversations if c.with_user)
            if not page.has_more or page.next_cursor is None:
                break
            cursor = page.next_cursor
        self.store.save_conversations(account_id, list(listed.values()))
        self._publish(account_id, {"type": "conversations"})

        stale = self.store.stale(account_id, list(listed)[:BACKFILL_CONVERSATIONS])
        for cid in stale:
            await asyncio.sleep(self._interval)
            await self.fetch_newest(account_id, cid, conn)
        nameless = self.store.nameless(account_id, list(listed))[:NAME_LOOKUPS]
        if nameless:
            self._spawn(self.lookup_names(account_id, nameless))
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
            if self.store.nameless(account_id, [cid]):
                await self.lookup_names(account_id, [cid])
        return {"added": added, "older_more": self.store.older(account_id, cid)[1]}

    async def lookup_names(self, account_id: str, cids: list[str]) -> int:
        """向平台查这些会话里对方的昵称，一个会话一个 mtop 请求（网页版也是这样），
        走账号的锁、限流和熔断。

        查之前再看一眼还缺不缺（可能已经有了）。出错就先停下，下次再查。返回查了几个。
        """
        found = 0
        async with self._name_locks[account_id]:
            for cid in cids:
                conversation = self.store.conversation(account_id, cid)
                if conversation is None or not self.store.nameless(account_id, [cid]):
                    continue
                if found:
                    await asyncio.sleep(self._interval)
                try:
                    async with self._locks[account_id], self._accounts.mtop(account_id) as mtop:
                        name = await fetch_peer_nick(
                            mtop,
                            cid,
                            session_type=conversation.biz_type,
                            peer_type=conversation.peer_type,
                        )
                except (XianyuError, httpx.HTTPError) as exc:
                    log.warning("查会话 %s 对方的昵称失败，先停下：%s", cid, exc)
                    break
                self.store.set_peer_name(account_id, cid, name)
                found += 1
        if found:
            self._publish(account_id, {"type": "conversations"})
        return found

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

    # ---- 发消息 ----

    async def send(self, account_id: str, cid: str, text: str) -> MessageRecord:
        """在商品会话里发一条文本消息，存进本地库、通知页面，返回存下的这条。

        同一个账号的消息排队一条条发，发之前过限流和熔断。没等到平台的结果时抛
        ImSendUncertainError（消息可能已经发出去了），不自动重发。
        """
        text = text.strip()
        if not text:
            raise ValueError("消息不能是空的")
        if len(text) > MAX_TEXT:
            raise ValueError(f"一条消息最多 {MAX_TEXT} 个字，现在有 {len(text)} 个")
        conversation = self.store.conversation(account_id, cid)
        if conversation is None:
            raise ValueError(f"本地没有会话 {cid}")
        if conversation.biz_type != "1" or not conversation.peer_id:
            raise ValueError("只能回复商品会话")
        self._online(account_id)
        async with self._send_locks[account_id]:
            guard = self._accounts.guard
            if guard:
                await guard.acquire(account_id, SEND_API)
            conn = self._online(account_id)  # 排队、等限流的时候可能断了
            message = await conn.send_text(cid, text, me=account_id, peer=conversation.peer_id)
        log.info("账号 %s 在会话 %s 发了一条消息 %s", account_id, cid, message.message_id)
        # 实测平台不会把这条推回发它的连接，所以用响应存库；万一推回来了，按消息 ID 去重只存一次
        await self._on_event(account_id, message)
        record = self.store.message(account_id, message.message_id)
        assert record is not None
        return record
