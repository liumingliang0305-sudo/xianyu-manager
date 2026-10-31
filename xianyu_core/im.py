"""即时消息：网页版闲鱼 /im 用的 WebSocket 长连接（钉钉 IMPaaS）。

新消息由服务端实时推下来，不用轮询。协议和实测结果见 docs/protocol-notes.md 的"即时消息"。

- `ImConnection`：一条已注册的连接。打开时完成握手、/reg、等 /s/vulcan 就绪；之后自己发心跳，
  每帧推送都回 ACK，推送带 syncExtraType 1 或 2 时对齐同步位点。推送里的条目解码后
  从 `events()` 出来；
- `ImClient`：一个账号长期在线，断了自动重连，每次连接都重新取 accessToken（网页版也是这样）；
- 聊天消息解析成 `ChatMessage`，会话列表解析成 `Conversation`；哪些会话是和人的聊天见
  `USER_SESSION_TYPES`；
- 发文本消息：`ImConnection.send_text()`，格式照网页版。

2026-10-30 实测过取 token、注册、收推送、会话列表、历史消息、心跳，2026-10-31 实测发过一条文本消息。
标已读还没有做。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import random
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import msgpack
import websockets

from xianyu_core.constants import IM_APP_KEY, IM_WS_URL, USER_AGENT, WEB_HOST
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    ImError,
    ImRejectedError,
    ImSendUncertainError,
    RateLimitedError,
    RiskControlError,
    XianyuError,
)
from xianyu_core.mtop import MtopClient

log = logging.getLogger(__name__)

TOKEN_API = "mtop.taobao.idlemessage.pc.login.token"
USER_QUERY_API = "mtop.taobao.idlemessage.pc.user.query"
SPM_IM = "a21ybx.im.0.0"
SEND_API = "/r/MessageSend/sendByReceiverScope"

HEARTBEAT_INTERVAL = 15.0
RESPONSE_TIMEOUT = 30.0  # 请求（含心跳）多久没回应算断了
READY_TIMEOUT = 30.0  # 注册后多久等不到 /s/vulcan 算失败
# 游标取 JS 的最大安全整数，表示从最新一页开始
NEWEST_CURSOR = 9007199254740991

# 连不上时按连续失败次数退避，超出长度后一直用最后一项（Ydisks 的数）
RECONNECT_DELAYS = (5.0, 15.0, 30.0, 60.0, 120.0)
# 连上超过这么久才断的，算正常断线，马上重连；刚连上就断的按上面退避
STABLE_AFTER = 60.0
# 被踢下线马上重连（网页版也是），但 KICK_WINDOW 秒里被踢 MAX_KICKS 次就停，免得和别处的登录抢
KICK_WINDOW = 600.0
MAX_KICKS = 3
# accessToken 到期前这么久主动断开，换新 token 重连。算出来不到 MIN_ROTATE_AFTER 秒就不主动换了，
# 免得 token 本来就短命时连上就断
TOKEN_ROTATE_LEAD = 600.0
MIN_ROTATE_AFTER = 60.0

# 聊天消息推送里发送端设备的信息，不往外给
SENDER_DEVICE_KEYS = frozenset({"clientIp", "port", "umid", "umidToken", "utdid"})

# 和人聊天的会话类型（会话的 bizType，消息提醒里的 sessionType）。网页版会话列表只显示
# 1、3、15、19、24、32、44、51、52 这几种，这里再去掉 3（"通知消息"，平台的活动推广）和
# 24（"闲小蜜"，客服机器人）。别的类型（活动推送号、通知号之类）网页版也不显示
USER_SESSION_TYPES = frozenset({"1", "15", "19", "32", "44", "51", "52"})
SYSTEM_USER_ID = "0"  # 系统会话里"对方"的 ID

# 人自己打的文字、发的图片（内层内容类型 1、2），提醒里的 reminderNotice 是这句，这时 reminderTitle
# 才是发送人昵称。自动回复的 reminderNotice 是"～"、标题是"你有一条新消息"；交易卡片之类有时也是
# 这句，标题却是"交易消息"，所以还要看内容类型
PERSON_NOTICE = "发来一条新消息"
PERSON_CONTENT_TYPES = frozenset({1, 2})

_REJECT_TOKEN_WORDS = ("invalid token", "token invalid", "not auth", "device id or appkey")
_REJECT_LIMIT_WORDS = ("connect limit", "too many", "session remove")

# 发消息被拒绝时 moreInfo 里的这几种要人去 App 里处理，网页版也是弹窗让人去 App
_SEND_REFUSALS = (
    ("USER_NEED_ALIPYA_BIND", "为了交易安全，要先在闲鱼 App 里绑定支付宝并完成实名认证"),
    ("USER_NEED_REALNAME_VERIFY", "平台要求先完成实名认证，在闲鱼 App 里发一条消息，按提示操作"),
    ("USER_NEED_VERIFY", "平台要求先完成身份验证，在闲鱼 App 里发一条消息，按提示操作"),
)

_uuid_seq = itertools.count(1)


def new_device_id(unb: str) -> str:
    """生成设备 ID：大写的随机 UUID 加 `-<unb>`。

    每个账号只在第一次连接时生成一次，存下来一直用；每次连接都换一个，看起来就像换了设备。
    """
    return f"{str(uuid.uuid4()).upper()}-{unb}"


def new_mid() -> str:
    """请求的消息 ID，服务端在响应里原样带回来，靠它把响应和请求对上。"""
    return f"{random.randint(0, 999)}{int(time.time() * 1000)} 0"


def new_uuid() -> str:
    """发消息带的 uuid：网页版是 `-`、毫秒时间戳、再接一个从 1 开始递增的序号。"""
    return f"-{int(time.time() * 1000)}{next(_uuid_seq)}"


def reg_user_agent(user_agent: str) -> str:
    """/reg 的 ua：浏览器 UA 后面接网页版 IM SDK（DingWeb 2.2.0）的标识，照网页版的格式拼。"""
    if match := re.search(r"Mac OS X ([\d_.]+)", user_agent):
        os_name, os_version = "Mac OS", match.group(1).replace("_", ".")
    elif match := re.search(r"Windows NT ([\d.]+)", user_agent):
        os_name, os_version = "Windows", "10" if match.group(1) == "10.0" else match.group(1)
    else:
        os_name, os_version = "other", "other"
    match = re.search(r"Chrome/([\d.]+)", user_agent)
    browser = f"Chrome/{match.group(1)}" if match else "other/other"
    return (
        f"{user_agent} DingTalk(2.2.0) OS({os_name}/{os_version}) Browser({browser}) "
        "DingWeb/2.2.0 IMPaaS DingWeb/2.2.0"
    )


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_at: float | None  # 秒级时间戳，平台没给或者看不懂时是 None


async def fetch_access_token(mtop: MtopClient, device_id: str) -> AccessToken:
    """取一个连接用的 accessToken。这里的 deviceId 必须和 /reg 的 did 一样。"""
    data = await mtop.call(TOKEN_API, {"appKey": IM_APP_KEY, "deviceId": device_id}, spm_cnt=SPM_IM)
    token = str(data.get("accessToken") or "")
    if not token:
        raise XianyuError(f"[{TOKEN_API}] 没有返回 accessToken", raw=data)
    return AccessToken(token, _expire_time(data.get("accessTokenExpiredTime")))


def _expire_time(value: Any, now: float | None = None) -> float | None:
    """过期时间可能是毫秒或秒级时间戳，也可能是剩余时长，按数量级猜。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    now = time.time() if now is None else now
    if number > now * 1000 / 2:
        return number / 1000
    if number > now / 2:
        return number
    if number >= 1_000_000:  # 剩余毫秒
        return now + number / 1000
    return now + number  # 剩余秒


# ---------- 推送的解码和解析 ----------


def decode_entry(data: str) -> Any:
    """推送条目的 data：base64 解出来，是 JSON 就按 JSON，不是就按 MessagePack。"""
    raw = base64.b64decode(data + "=" * (-len(data) % 4))
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    return _plain(msgpack.unpackb(raw, raw=False, strict_map_key=False))


def _plain(value: Any) -> Any:
    """MessagePack 解出来的结构转成 JSON 能表示的：键都变成字符串，二进制按 UTF-8 解。"""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def strip_goofish(value: Any) -> str:
    """`123@goofish` → `123`。"""
    return str(value or "").removesuffix("@goofish")


@dataclass
class ChatMessage:
    """一条聊天消息。推送（MessagePack，键是数字）和历史消息（JSON）两种格式都解析成它，
    字段位置见协议笔记的"聊天消息的推送"。"""

    message_id: str
    cid: str  # 会话 ID，不带 @goofish
    sender_id: str
    sender_name: str  # 发送人昵称；交易卡片、自动回复之类看不出来，是空字符串（见 PERSON_NOTICE）
    sent_at: float | None  # 秒级时间戳
    content_type: int | None  # 内层内容类型，1 是文本
    text: str  # 文本消息是正文，别的类型是平台给的摘要
    item_id: str
    session_type: str = ""  # 所在会话的类型，见 USER_SESSION_TYPES；没带是空字符串
    raw: dict[str, Any] = field(repr=False, default_factory=dict)  # 去掉了发送端设备信息

    @classmethod
    def parse(cls, data: Any) -> ChatMessage | None:
        """推送里的条目。不是聊天消息返回 None。"""
        body = data.get("1") if isinstance(data, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get("10"), dict):
            return None
        sender = body.get("1") if isinstance(body.get("1"), dict) else {}
        outer = body.get("6") if isinstance(body.get("6"), dict) else {}
        inner = outer.get("3") if isinstance(outer.get("3"), dict) else {}
        return cls._build(
            message_id=body.get("3"),
            cid=body.get("2"),
            sender_uid=sender.get("1"),
            sent_ms=body.get("5"),
            content=_json_dict(inner.get("5")),  # 推送里内层内容是 JSON 字符串
            summary=inner.get("2"),
            inner_type=inner.get("4"),
            reminder=body["10"],
            raw={**data, "1": {**body, "10": _scrub_reminder(body["10"])}},
        )

    @classmethod
    def from_history(cls, model: Any) -> ChatMessage | None:
        """历史消息（listUserMessages 的 userMessageModels）里的一条。"""
        message = model.get("message") if isinstance(model, dict) else None
        if not isinstance(message, dict):
            return None
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        custom = content.get("custom") if isinstance(content.get("custom"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        reminder = message.get("extension") if isinstance(message.get("extension"), dict) else {}
        scrubbed = {**message, "extension": _scrub_reminder(reminder)}
        return cls._build(
            message_id=message.get("messageId"),
            cid=message.get("cid"),
            sender_uid=sender.get("uid"),
            sent_ms=message.get("createAt"),
            content=_base64_json_dict(custom.get("data")),  # 历史消息里是 base64 的 JSON
            summary=custom.get("summary"),
            inner_type=custom.get("type"),
            reminder=reminder,
            raw={**model, "message": scrubbed},
        )

    @classmethod
    def _build(
        cls,
        *,
        message_id: Any,
        cid: Any,
        sender_uid: Any,
        sent_ms: Any,
        content: dict[str, Any],
        summary: Any,
        inner_type: Any,
        reminder: dict[str, Any],
        raw: dict[str, Any],
    ) -> ChatMessage | None:
        cid = strip_goofish(cid)
        if not cid:
            return None
        text = content["text"].get("text") if isinstance(content.get("text"), dict) else None
        if text is None:
            text = summary or reminder.get("reminderContent") or ""
        url_args = parse_qs(urlsplit(str(reminder.get("reminderUrl") or "")).query)
        content_type = _int_or_none(content.get("contentType", inner_type))
        by_person = (
            reminder.get("reminderNotice") == PERSON_NOTICE and content_type in PERSON_CONTENT_TYPES
        )
        return cls(
            message_id=str(message_id or ""),
            cid=cid,
            sender_id=str(reminder.get("senderUserId") or strip_goofish(sender_uid)),
            sender_name=str(reminder.get("reminderTitle") or "") if by_person else "",
            sent_at=sent_ms / 1000 if isinstance(sent_ms, int | float) else None,
            content_type=content_type,
            text=str(text),
            item_id=(url_args.get("itemId") or [""])[0],
            session_type=str(reminder.get("sessionType") or ""),
            raw=raw,
        )


def _scrub_reminder(reminder: dict[str, Any]) -> dict[str, Any]:
    """去掉提醒信息（和它的 extJson）里发送端的 IP 和设备标识，返回新的字典。"""
    scrubbed = {k: v for k, v in reminder.items() if k not in SENDER_DEVICE_KEYS}
    ext = _json_dict(scrubbed.get("extJson"))
    if ext:
        scrubbed["extJson"] = json.dumps(
            {k: v for k, v in ext.items() if k not in SENDER_DEVICE_KEYS}, ensure_ascii=False
        )
    return scrubbed


@dataclass
class MessagePage:
    messages: list[ChatMessage]  # 平台给的顺序
    has_more: bool
    next_cursor: int | None


@dataclass
class ImPush:
    """推送里不是聊天消息的条目：会话激活、已读回执之类，原样给出解码后的内容。"""

    lwp: str
    data: Any


ImEvent = ChatMessage | ImPush


@dataclass
class Conversation:
    """会话列表里的一个单聊会话。"""

    cid: str
    peer_id: str
    # 对方昵称：最后一条消息是对方本人发的才知道，别的时候是空字符串，要查见 fetch_peer_nick
    peer_name: str
    peer_type: str  # 对方的用户类型，查昵称要用；普通用户是 "0"，系统账号是 "10"
    biz_type: str  # "1" 是商品会话，别的是系统通知之类
    item_id: str
    item_title: str
    item_pic: str
    order_id: str
    unread: int
    updated_at: float | None  # 秒级时间戳
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def with_user(self) -> bool:
        """是不是和人的聊天。不是的是平台的活动推广、通知号、客服机器人之类，对方 ID 是 "0"。"""
        return self.biz_type in USER_SESSION_TYPES and self.peer_id not in ("", SYSTEM_USER_ID)

    @classmethod
    def parse(cls, row: dict[str, Any], unb: str) -> Conversation | None:
        user_conv = row.get("singleChatUserConversation")
        if not isinstance(user_conv, dict):
            return None
        conv = user_conv.get("singleChatConversation") or {}
        cid = strip_goofish(conv.get("cid"))
        if not cid:
            return None
        pair = [strip_goofish(conv.get("pairFirst")), strip_goofish(conv.get("pairSecond"))]
        peer = next((p for p in pair if p and p != unb), "")
        ext = conv.get("extension") or {}
        modified = user_conv.get("modifyTime")
        # 会话两边一个是 owner、一个是 ext，extUserId 是自己的话对方就是 owner
        own_ext = str(ext.get("extUserId") or "") == unb
        peer_type = ext.get("ownerUserType") if own_ext else ext.get("extUserType")
        # 注意 squadName_<userId> 不是昵称，是商品的简称（"取暖器"之类）
        return cls(
            cid=cid,
            peer_id=peer,
            peer_name=_last_message_nick(user_conv.get("lastMessage"), peer),
            peer_type="" if peer_type is None else str(peer_type),
            biz_type=str(conv.get("bizType") or ""),
            item_id=str(ext.get("itemId") or ""),
            item_title=str(ext.get("itemTitle") or ""),
            item_pic=str(ext.get("itemMainPic") or ""),
            order_id=str(ext.get("orderId") or ""),
            unread=_int_or_none(user_conv.get("redPoint")) or 0,
            updated_at=modified / 1000 if isinstance(modified, int | float) else None,
            raw=row,
        )


def _last_message_nick(last_message: Any, peer: str) -> str:
    """会话最后一条消息是对方本人发的，就能看出对方昵称（见 ChatMessage.sender_name）。"""
    message = ChatMessage.from_history(last_message) if isinstance(last_message, dict) else None
    if message and peer and message.sender_id == peer:
        return message.sender_name
    return ""


@dataclass
class ConversationPage:
    conversations: list[Conversation]
    has_more: bool
    next_cursor: int | None


async def fetch_peer_nick(mtop: MtopClient, cid: str, *, session_type: str, peer_type: str) -> str:
    """会话里对方的昵称，和网页版会话列表、聊天标题显示的一样：有闲鱼昵称（fishNick）用它，
    没有用打了码的账号名（nick，"x***0" 这样）。查不到是空字符串。

    网页版也是一个会话调一次 `mtop.taobao.idlemessage.pc.user.query`（4.0），按会话 ID 和对方的
    用户类型查，isOwner 是"查的是不是自己"。
    """
    data = await mtop.call(
        USER_QUERY_API,
        {
            "type": int(peer_type or 0),
            "sessionType": int(session_type or 1),
            "sessionId": cid,
            "isOwner": False,
        },
        version="4.0",
        spm_cnt=SPM_IM,
    )
    info = data.get("userInfo") if isinstance(data.get("userInfo"), dict) else {}
    return str(info.get("fishNick") or info.get("nick") or "")


def _json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.startswith("{"):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _base64_json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        return _json_dict(base64.b64decode(value + "=" * (-len(value) % 4)).decode())
    except (ValueError, UnicodeDecodeError):
        return {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------- 一条连接 ----------

_CLOSED = object()


class ImConnection:
    """一条已注册的连接。用 `ImConnection.open()` 打开，用完 `close()`。

    `events()` 只能有一个人在读；连接断了以后它抛出断开的原因（`close_reason`，一个 ImError）。
    """

    def __init__(
        self,
        ws: Any,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        response_timeout: float = RESPONSE_TIMEOUT,
    ):
        self._ws = ws
        self._heartbeat_interval = heartbeat_interval
        self._response_timeout = response_timeout
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._ready = asyncio.Event()  # 收到了 /s/vulcan
        self._ended = asyncio.Event()  # 断开了，原因在 close_reason
        self._tasks: set[asyncio.Task[Any]] = set()
        self._syncing = False
        self._sync_again = False
        self.close_reason: ImError | None = None
        self.opened_at = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())

    @classmethod
    async def open(
        cls,
        *,
        cookies: dict[str, str],
        token: str,
        device_id: str,
        user_agent: str = USER_AGENT,
        url: str = IM_WS_URL,
        connect: Callable[..., Awaitable[Any]] = websockets.connect,
        ready_timeout: float = READY_TIMEOUT,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        response_timeout: float = RESPONSE_TIMEOUT,
    ) -> ImConnection:
        """握手、注册、等就绪。`connect` 留给测试换成假的 WebSocket。"""
        ws = await connect(
            url,
            additional_headers={
                "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                "Origin": WEB_HOST,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            user_agent_header=user_agent,
            ping_interval=None,  # 心跳走协议自己的 /!
            max_size=8 << 20,
            open_timeout=response_timeout,
        )
        conn = cls(ws, heartbeat_interval=heartbeat_interval, response_timeout=response_timeout)
        try:
            await conn._register(token, device_id, user_agent)
            conn._spawn(conn._heartbeat_loop())
            await conn._wait_ready(ready_timeout)
        except BaseException:
            await conn.close()
            raise
        return conn

    async def _register(self, token: str, device_id: str, user_agent: str) -> None:
        headers = {
            "cache-header": "app-key token ua wv",
            "app-key": IM_APP_KEY,
            "token": token,
            "ua": reg_user_agent(user_agent),
            "dt": "j",
            "wv": "im:3,au:3,sy:6",
            "sync": "0,0;0;0;",
            "did": device_id,
        }
        try:
            await self.request("/reg", headers=headers)
        except ImError as exc:
            if exc.code is None:
                raise
            text = _frame_reason(exc.raw).lower()
            if exc.code == 401 or any(word in text for word in _REJECT_TOKEN_WORDS):
                reason = ImRejectedError.INVALID_TOKEN
            elif any(word in text for word in _REJECT_LIMIT_WORDS):
                reason = ImRejectedError.SESSION_REMOVED
            else:
                raise
            raise ImRejectedError(
                f"注册被拒绝：{exc}", reason=reason, code=exc.code, raw=exc.raw
            ) from None

    async def _wait_ready(self, timeout: float) -> None:
        ready = asyncio.create_task(self._ready.wait())
        ended = asyncio.create_task(self._ended.wait())
        done, _ = await asyncio.wait(
            {ready, ended}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        ready.cancel()
        ended.cancel()
        if self.close_reason:
            raise self.close_reason
        if ready not in done:
            raise ImError(f"注册后 {timeout:.0f} 秒没有等到 /s/vulcan")

    # ----- 请求 -----

    async def request(
        self,
        lwp: str,
        body: Any = None,
        *,
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发一个请求，返回服务端的响应帧。没回应或者不是 200 抛 ImError。"""
        if self.close_reason:
            raise self.close_reason
        mid = new_mid()
        key = mid.split()[0]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        frame: dict[str, Any] = {"lwp": lwp, "headers": {**(headers or {}), "mid": mid}}
        if body is not None:
            frame["body"] = body
        wait = timeout or self._response_timeout
        try:
            await self._send(frame)
            async with asyncio.timeout(wait):
                resp = await future
        except TimeoutError:
            raise ImError(f"{lwp} {wait:.0f} 秒没有回应") from None
        finally:
            self._pending.pop(key, None)
        code = resp.get("code")
        if code != 200:
            reason = _frame_reason(resp)
            raise ImError(
                f"{lwp} 返回 {code}" + (f"：{reason}" if reason else ""),
                code=_int_or_none(code),
                raw=resp,
            )
        return resp

    async def list_conversations(
        self, unb: str, cursor: int = NEWEST_CURSOR, limit: int = 20
    ) -> ConversationPage:
        """会话列表，最近有动静的在前。翻下一页把上一页的 `next_cursor` 传进来。"""
        resp = await self.request("/r/Conversation/listNewestPagination", [cursor, limit])
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        rows = [row for row in body.get("userConvs") or [] if isinstance(row, dict)]
        conversations = [c for c in (Conversation.parse(row, unb) for row in rows) if c]
        return ConversationPage(
            conversations=conversations,
            has_more=bool(body.get("hasMore")),
            next_cursor=_int_or_none(body.get("nextCursor")),
        )

    async def list_messages(
        self, cid: str, cursor: int = NEWEST_CURSOR, limit: int = 20
    ) -> MessagePage:
        """一个会话的历史消息，从最新的往前翻。翻下一页把上一页的 `next_cursor` 传进来。"""
        resp = await self.request(
            "/r/MessageManager/listUserMessages", [f"{cid}@goofish", False, cursor, limit, False]
        )
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        models = body.get("userMessageModels") or []
        messages = [m for m in map(ChatMessage.from_history, models) if m]
        return MessagePage(
            messages=messages,
            has_more=bool(body.get("hasMore")),
            next_cursor=_int_or_none(body.get("nextCursor")),
        )

    async def send_text(self, cid: str, text: str, *, me: str, peer: str) -> ChatMessage:
        """在单聊会话里发一条文本消息，返回平台收下的这条。

        返回的和推送、历史消息解析成同一个 ChatMessage。格式照网页版：外层是自定义消息 101，
        内层 `{"contentType": 1, "text": {"text": …}}` 转成 JSON 再 base64；接收方是对方和自己。
        - 连接已经断了：消息没发出去，抛 ImError；
        - 服务端拒绝（不是 200，或者没给消息 ID 但说了原因）：抛 ImError，带上能看懂的原因；
        - 请求发出去了但没等到结果：抛 ImSendUncertainError，消息可能已经发出去了，不要自动重发。
        """
        if self.close_reason:
            raise ImError(f"即时消息连接断了，消息没有发出去：{self.close_reason}")
        inner = json.dumps(
            {"contentType": 1, "text": {"text": text}}, ensure_ascii=False, separators=(",", ":")
        )
        data = base64.b64encode(inner.encode()).decode()
        content = {"contentType": 101, "custom": {"type": 1, "data": data}}
        model = {
            "uuid": new_uuid(),
            "cid": f"{cid}@goofish",
            "conversationType": 1,
            "content": content,
            "redPointPolicy": 0,
            "extension": {"extJson": "{}"},
            "ctx": {"appVersion": "1.0", "platform": "web"},
            "mtags": {},
            "msgReadStatusSetting": 1,
        }
        receivers = {"actualReceivers": [f"{peer}@goofish", f"{me}@goofish"]}
        try:
            resp = await self.request(SEND_API, [model, receivers])
        except ImError as exc:
            if exc.code is None:
                raise ImSendUncertainError(f"不知道消息发出去没有：{exc}", raw=exc.raw) from None
            reason = _send_refusal(exc.raw) or f"服务端返回 {exc.code}"
            raise ImError(f"消息没有发出去：{reason}", code=exc.code, raw=exc.raw) from None

        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        extension = body.get("extension")
        log.debug(
            "发消息的响应：body 有 %s，extension 有 %s",
            sorted(body),
            sorted(extension) if isinstance(extension, dict) else None,
        )
        message_id = body.get("messageId")
        if not message_id:
            if reason := _send_refusal(resp):
                raise ImError(f"消息没有发出去：{reason}", code=200, raw=resp)
            raise ImSendUncertainError("服务端回了 200，但没给消息 ID，不知道发出去没有", raw=resp)
        created = body.get("createAt")
        message = ChatMessage.from_history(
            {
                "message": {
                    "messageId": message_id,
                    "cid": model["cid"],
                    "createAt": created
                    if isinstance(created, int | float)
                    else int(time.time() * 1000),
                    "sender": {"uid": f"{me}@goofish"},
                    "content": {**content, "custom": {**content["custom"], "summary": text}},
                    "extension": extension if isinstance(extension, dict) else {},
                }
            }
        )
        assert message is not None  # cid 不是空的就一定解析得出来
        return message

    # ----- 收推送 -----

    async def events(self) -> AsyncIterator[ImEvent]:
        while True:
            item = await self._events.get()
            if item is _CLOSED:
                self._events.put_nowait(_CLOSED)  # 再读也是这个结果
                assert self.close_reason is not None
                raise self.close_reason
            yield item

    async def _read_loop(self) -> None:
        reason: ImError
        try:
            async for raw in self._ws:
                await self._on_frame(raw)
        except websockets.ConnectionClosed as exc:
            reason = ImError(f"连接断了：{exc}")
        except Exception as exc:  # 读的时候出错只能当连接断了
            log.warning("即时消息连接读取出错", exc_info=True)
            reason = ImError(f"连接出错：{exc!r}")
        else:
            reason = ImError("服务端关闭了连接")
        self._finish(reason)

    async def _on_frame(self, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            log.debug("收到不是 JSON 的帧，长度 %d", len(raw))
            return
        if not isinstance(frame, dict):
            return
        headers = frame.get("headers")
        if not isinstance(headers, dict):
            return
        if "code" in frame:  # 响应
            future = self._pending.get(str(headers.get("mid", "")).split(" ")[0])
            if future and not future.done():
                future.set_result(frame)
            return
        lwp = frame.get("lwp")
        if not isinstance(lwp, str) or not lwp:
            return
        # 推送：先回 ACK，原样带回服务端的 headers；一帧只回一次，不管里面有几条
        await self._send({"code": 200, "headers": dict(headers)})
        if lwp == "/push/kickout":
            self._finish(ImRejectedError("被服务端踢下线", reason=ImRejectedError.KICKOUT))
            return
        if lwp == "/s/session/remove":
            self._finish(
                ImRejectedError("会话被服务端移除", reason=ImRejectedError.SESSION_REMOVED)
            )
            return
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        extra = body.get("syncExtraType")
        if isinstance(extra, dict) and extra.get("type") in (1, 2):
            self._start_sync()
        package = body.get("syncPushPackage")
        package = package if isinstance(package, dict) else {}
        for index, entry in enumerate(package.get("data") or []):
            data = entry.get("data") if isinstance(entry, dict) else None
            if not isinstance(data, str) or not data:
                continue
            try:
                decoded = decode_entry(data)
            except Exception:
                log.warning("%s 第 %d 条解不出来", lwp, index, exc_info=True)
                continue
            message = ChatMessage.parse(decoded)
            self._events.put_nowait(message or ImPush(lwp, decoded))
        if lwp == "/s/vulcan":
            self._ready.set()

    def _start_sync(self) -> None:
        """对齐同步位点：getState 再 ackDiff。正在对齐时又要对齐，就等这一轮完了再来一轮。"""
        if self._syncing:
            self._sync_again = True
            return
        self._syncing = True
        self._spawn(self._sync_loop())

    async def _sync_loop(self) -> None:
        try:
            while True:
                self._sync_again = False
                try:
                    state = await self.request("/r/SyncStatus/getState", [{"topic": "sync"}])
                    await self.request("/r/SyncStatus/ackDiff", [state.get("body")])
                except ImError as exc:
                    if self.close_reason is None:
                        log.warning("对齐同步位点失败：%s", exc)
                if not self._sync_again or self.close_reason:
                    return
        finally:
            self._syncing = False

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self.request("/!")
            except ImError as exc:
                if self.close_reason is None:
                    self._finish(ImError(f"心跳失败：{exc}"))
                    await self._close_ws()
                return

    # ----- 收尾 -----

    async def close(self) -> None:
        self._finish(ImError("连接已关闭"))
        await self._close_ws()
        self._reader.cancel()
        tasks = [*self._tasks, self._reader]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_ws(self) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close()

    def _finish(self, reason: ImError) -> None:
        """记下断开的原因（只记第一个），叫醒所有在等的人。"""
        if self.close_reason is not None:
            return
        self.close_reason = reason
        self._ended.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(reason)
        self._events.put_nowait(_CLOSED)
        if isinstance(reason, ImRejectedError):
            self._spawn(self._close_ws())

    @property
    def closed(self) -> bool:
        return self.close_reason is not None

    async def _send(self, frame: dict[str, Any]) -> None:
        try:
            await self._ws.send(json.dumps(frame, ensure_ascii=False))
        except websockets.ConnectionClosed as exc:
            self._finish(ImError(f"连接断了：{exc}"))
            raise self.close_reason from None  # type: ignore[misc]

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _frame_reason(frame: Any) -> str:
    """响应帧里能找到的出错说明。"""
    if not isinstance(frame, dict):
        return ""
    parts = []
    for source in (frame, frame.get("body"), frame.get("headers")):
        if not isinstance(source, dict):
            continue
        for key in ("reason", "message", "msg", "error", "error-message", "moreInfo"):
            value = source.get(key)
            if value not in (None, "") and str(value) not in parts:
                parts.append(str(value))
    return " | ".join(parts)


def _send_refusal(frame: Any) -> str:
    """发消息被拒绝的原因。要人去 App 里处理的几种换成能看懂的说法，别的原样给出。"""
    reason = _frame_reason(frame)
    for key, text in _SEND_REFUSALS:
        if key in reason:
            return text
    return reason


# ---------- 长期在线 ----------


@dataclass
class ImStatus:
    state: str  # connecting / online / waiting（等着重连）/ stopped
    since: float
    message: str = ""


EventHandler = Callable[[ImConnection, ImEvent], Awaitable[None]]
ConnectedHandler = Callable[[ImConnection], Awaitable[None]]


class ImClient:
    """一个账号长期在线。`run()` 一直跑到被取消，或者碰到要人处理的情况才抛出来：

    - 取 token 时登录失效、风控、熔断（AuthRequiredError、RiskControlError、CircuitOpenError）；
    - 连接数超限（/s/session/remove）、注册时 token 连续两次无效、短时间里被踢下线太多次
      （ImRejectedError）。

    别的情况自己重连：网络断了、心跳没回应，连上超过 STABLE_AFTER 秒的马上重连，刚连上就断的按
    RECONNECT_DELAYS 退避；被踢下线马上重连（网页版也是）；accessToken 快到期时主动断开换新的。

    `fetch_token` 和 `load_cookies` 由业务层给：取 token 要走它的限流、熔断，cookie 要用最新的。
    每次连上（包括重连）先调 `on_connected`，适合在这里补拉断线期间漏掉的消息；之后每个推送条目
    调一次 `on_event`；状态（`status`）每次变化调一次 `on_status`。
    回调抛的异常只记日志，不影响连接。
    """

    def __init__(
        self,
        *,
        device_id: str,
        fetch_token: Callable[[], Awaitable[AccessToken]],
        load_cookies: Callable[[], dict[str, str]],
        user_agent: str = USER_AGENT,
        url: str = IM_WS_URL,
        connect: Callable[..., Awaitable[Any]] = websockets.connect,
        reconnect_delays: tuple[float, ...] = RECONNECT_DELAYS,
        stable_after: float = STABLE_AFTER,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        response_timeout: float = RESPONSE_TIMEOUT,
        ready_timeout: float = READY_TIMEOUT,
        on_status: Callable[[ImStatus], None] | None = None,
    ):
        self.device_id = device_id
        self._fetch_token = fetch_token
        self._load_cookies = load_cookies
        self._open_args = {
            "user_agent": user_agent,
            "url": url,
            "connect": connect,
            "ready_timeout": ready_timeout,
            "heartbeat_interval": heartbeat_interval,
            "response_timeout": response_timeout,
        }
        self._delays = reconnect_delays
        self._stable_after = stable_after
        self._on_status = on_status
        self.status = ImStatus("stopped", time.time())
        self.connection: ImConnection | None = None

    async def run(
        self, on_event: EventHandler, on_connected: ConnectedHandler | None = None
    ) -> None:
        failures = 0  # 连续连不上的次数
        bad_tokens = 0  # 连续注册时 token 无效的次数
        kicks: deque[float] = deque()
        try:
            while True:
                self._set("connecting")
                try:
                    token = await self._fetch_token()
                except RateLimitedError as exc:
                    await self._wait(exc.retry_after, str(exc))
                    continue
                except (AuthRequiredError, RiskControlError, CircuitOpenError) as exc:
                    self._set("stopped", str(exc))
                    raise
                except (XianyuError, httpx.HTTPError) as exc:
                    failures += 1
                    await self._wait(self._delay(failures), f"取 accessToken 失败：{exc}")
                    continue

                try:
                    conn = await ImConnection.open(
                        cookies=self._load_cookies(),
                        token=token.token,
                        device_id=self.device_id,
                        **self._open_args,
                    )
                except ImRejectedError as exc:
                    bad_tokens += 1
                    if exc.reason == ImRejectedError.INVALID_TOKEN and bad_tokens < 2:
                        log.warning("注册时 token 无效，换一个再试：%s", exc)
                        continue
                    self._set("stopped", str(exc))
                    raise
                except (ImError, OSError, TimeoutError, websockets.WebSocketException) as exc:
                    failures += 1
                    await self._wait(self._delay(failures), f"连不上：{exc}")
                    continue

                bad_tokens = 0
                reason = await self._serve(conn, token, on_event, on_connected)
                if reason is None:
                    log.info("accessToken 快到期，换新的重连")
                    continue
                if isinstance(reason, ImRejectedError):
                    if reason.reason != ImRejectedError.KICKOUT:
                        self._set("stopped", str(reason))
                        raise reason
                    now = time.monotonic()
                    kicks.append(now)
                    while kicks and now - kicks[0] > KICK_WINDOW:
                        kicks.popleft()
                    if len(kicks) >= MAX_KICKS:
                        message = f"{KICK_WINDOW / 60:.0f} 分钟里被踢下线 {len(kicks)} 次，先停下"
                        self._set("stopped", message)
                        raise ImRejectedError(message, reason=ImRejectedError.KICKOUT)
                    log.warning("被服务端踢下线，马上重连")
                    continue
                if time.monotonic() - conn.opened_at >= self._stable_after:
                    failures = 0
                    await self._wait(1.0, str(reason))
                else:
                    failures += 1
                    await self._wait(self._delay(failures), str(reason))
        finally:
            if self.status.state != "stopped":
                self._set("stopped")

    async def _serve(
        self,
        conn: ImConnection,
        token: AccessToken,
        on_event: EventHandler,
        on_connected: ConnectedHandler | None,
    ) -> ImError | None:
        """用一条连接直到它断开，返回断开的原因；到了换 token 的时候返回 None。"""
        self.connection = conn
        self._set("online")
        rotate_after = None
        if token.expires_at:
            rotate_after = token.expires_at - time.time() - TOKEN_ROTATE_LEAD
            if rotate_after < MIN_ROTATE_AFTER:
                rotate_after = None
        try:
            if on_connected:
                try:
                    await on_connected(conn)
                except Exception:
                    log.exception("on_connected 出错")
            async with asyncio.timeout(rotate_after):
                async for event in conn.events():
                    try:
                        await on_event(conn, event)
                    except Exception:
                        log.exception("on_event 出错")
        except TimeoutError:
            return None
        except ImError as exc:
            return exc
        finally:
            self.connection = None
            await conn.close()
        return conn.close_reason or ImError("连接结束了")

    def _delay(self, failures: int) -> float:
        return self._delays[min(failures, len(self._delays)) - 1]

    async def _wait(self, seconds: float, message: str) -> None:
        log.warning("即时消息：%s，%.0f 秒后重连", message, seconds)
        self._set("waiting", message)
        await asyncio.sleep(seconds)

    def _set(self, state: str, message: str = "") -> None:
        self.status = ImStatus(state, time.time(), message)
        if self._on_status:
            try:
                self._on_status(self.status)
            except Exception:
                log.exception("on_status 出错")
