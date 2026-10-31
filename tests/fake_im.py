"""假的即时消息服务端，换掉 websockets.connect 用。"""

import asyncio
import base64
import json

import msgpack
import websockets

PUSH_HEADERS = {"app-key": "k", "ua": "server", "sid": "s1"}


def entry(data, *, packer: str = "json") -> dict:
    """推送里的一个条目：data 按 JSON 或 MessagePack 打包再 base64。"""
    raw = msgpack.packb(data) if packer == "msgpack" else json.dumps(data).encode()
    return {"data": base64.b64encode(raw).decode(), "objectType": 40000}


def chat_message(
    *,
    cid: str = "777",
    sender: str = "200",
    text: str = "还在吗",
    item: str = "300",
    message_id: str = "1793378126001.PNM",
    sent_ms: int = 1793378126437,
) -> dict:
    """照实测的格式造一条聊天消息（MessagePack 解出来的那种，键是整数）。"""
    inner = json.dumps({"contentType": 1, "text": {"text": text}, "atUsers": []})
    ext = {"messageId": message_id, "umidToken": "secret", "utdid": "secret", "tag": "u"}
    return {
        1: {
            1: {1: f"{sender}@goofish"},
            2: f"{cid}@goofish",
            3: message_id,
            5: sent_ms,
            6: {1: 101, 3: {1: "", 2: text, 3: "", 4: 1, 5: inner}},
            10: {
                "senderUserId": sender,
                "reminderTitle": "买家小王",
                "reminderContent": text,
                "reminderUrl": (
                    f"fleamarket://message_chat?itemId={item}&peerUserId={sender}"
                    f"&sid={cid}&messageId={message_id}&adv=no"
                ),
                "sessionType": "1",
                "extJson": json.dumps(ext),
                "clientIp": "10.0.0.1",
                "port": "51000",
                "umid": "secret",
                "umidToken": "secret",
                "utdid": "secret",
            },
        },
        3: {"needPush": "false"},
    }


def history_message(
    *,
    cid: str = "777",
    sender: str = "200",
    text: str = "还在吗",
    item: str = "300",
    message_id: str = "1793378126001.PNM",
    sent_ms: int = 1793378126437,
) -> dict:
    """照实测的格式造一条历史消息（listUserMessages 的 userMessageModels 里的一条）。"""
    data = json.dumps({"contentType": 1, "text": {"text": text}, "atUsers": []})
    reminder = chat_message(cid=cid, sender=sender, text=text, item=item, message_id=message_id)[1][
        10
    ]
    return {
        "readStatus": 2,
        "message": {
            "messageId": message_id,
            "cid": f"{cid}@goofish",
            "createAt": sent_ms,
            "sender": {"uid": f"{sender}@goofish", "tag": 0},
            "content": {
                "contentType": 101,
                "custom": {
                    "type": 1,
                    "summary": text,
                    "data": base64.b64encode(data.encode()).decode(),
                },
            },
            "extension": reminder,
        },
    }


def session_activation(cid: str = "777") -> dict:
    return {"sessionId": cid, "chatType": 1, "operation": {"content": {"contentType": 8}}}


def conversation(
    cid: str,
    me: str,
    peer: str,
    *,
    unread: int = 0,
    item: str = "300",
    modified_ms: int = 1793378003908,
) -> dict:
    return {
        "type": 1,
        "singleChatUserConversation": {
            "redPoint": unread,
            "modifyTime": modified_ms,
            "singleChatConversation": {
                "cid": f"{cid}@goofish",
                "pairFirst": f"{peer}@goofish",
                "pairSecond": f"{me}@goofish",
                "bizType": "1",
                "extension": {
                    "itemId": item,
                    "itemTitle": "洗衣机",
                    f"squadName_{peer}": "买家小王",
                    f"squadName_{me}": "我",
                },
            },
        },
    }


class FakeSocket:
    """一条假连接。客户端发的帧记在 sent（ACK 另外记在 acks），服务端用 push() 推帧。"""

    def __init__(self, server: "FakeIm", kwargs: dict):
        self.server = server
        self.kwargs = kwargs
        self.sent: list[dict] = []
        self.acks: list[dict] = []
        self.closed = False
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def send(self, text: str) -> None:
        if self.closed:
            raise websockets.ConnectionClosedOK(None, None)
        frame = json.loads(text)
        if "lwp" in frame:
            self.sent.append(frame)
            self.server.handle(self, frame)
        else:
            self.acks.append(frame)

    def requests(self, lwp: str) -> list[dict]:
        return [frame for frame in self.sent if frame["lwp"] == lwp]

    def push(self, lwp: str, body: dict | None = None) -> dict:
        frame = {"lwp": lwp, "headers": {**PUSH_HEADERS, "mid": f"p{len(self.sent)} 0"}}
        if body is not None:
            frame["body"] = body
        self._inbox.put_nowait(json.dumps(frame))
        return frame

    def push_entries(self, entries: list[dict], *, lwp: str = "/s/sync", sync_type=None) -> dict:
        body: dict = {"syncPushPackage": {"data": entries}}
        if sync_type is not None:
            body["syncExtraType"] = {"type": sync_type}
        return self.push(lwp, body)

    def respond(self, request: dict, code: int = 200, body=None) -> None:
        frame = {"code": code, "headers": {"mid": request["headers"]["mid"]}}
        if body is not None:
            frame["body"] = body
        self._inbox.put_nowait(json.dumps(frame))

    def drop(self) -> None:
        """服务端异常断开。"""
        self._inbox.put_nowait(ConnectionError)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._inbox.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self._inbox.get()
        if item is None:
            raise StopAsyncIteration
        if item is ConnectionError:
            self.closed = True
            raise websockets.ConnectionClosedError(None, None)
        return item


class FakeIm:
    """按 lwp 应答。改下面这些属性模拟各种情况。"""

    def __init__(self):
        self.sockets: list[FakeSocket] = []
        self.reg_code = 200
        self.reg_body: dict = {"unitName": "PNM"}
        self.ready = True  # 注册后推不推 /s/vulcan
        self.vulcan: list[dict] = [entry(session_activation())]
        self.heartbeat_ok = True
        self.conversations: list[dict] = []
        self.conversations_more = True  # 会话列表说不说还有下一页
        self.history: list[dict] = []
        # 会话 ID → 历史消息（history_message 造的）。在这里的会话按真实的游标规则分页
        self.chats: dict[str, list[dict]] = {}
        self.refuse = 0  # 前几次 connect 直接连不上

    async def connect(self, url: str, **kwargs) -> FakeSocket:
        if self.refuse:
            self.refuse -= 1
            raise OSError("连不上")
        sock = FakeSocket(self, {"url": url, **kwargs})
        self.sockets.append(sock)
        return sock

    @property
    def last(self) -> FakeSocket:
        return self.sockets[-1]

    def handle(self, sock: FakeSocket, frame: dict) -> None:
        lwp = frame["lwp"]
        if lwp == "/reg":
            sock.respond(frame, self.reg_code, self.reg_body)
            if self.reg_code == 200 and self.ready:
                sock.push("/s/sync", {"syncExtraType": {"type": 2}})
                sock.push_entries(self.vulcan, lwp="/s/vulcan", sync_type=3)
        elif lwp == "/r/SyncStatus/getState":
            sock.respond(frame, 200, {"topic": "sync", "pts": 1})
        elif lwp == "/r/SyncStatus/ackDiff":
            sock.respond(frame, 200)
        elif lwp == "/!":
            if self.heartbeat_ok:
                sock.respond(frame, 200)
        elif lwp == "/r/Conversation/listNewestPagination":
            body = {
                "userConvs": self.conversations,
                "hasMore": int(self.conversations_more),
                "nextCursor": 1790449136584,
            }
            sock.respond(frame, 200, body)
        elif lwp == "/r/MessageManager/listUserMessages":
            cid, _, cursor, limit, _ = frame["body"]
            cid = cid.removesuffix("@goofish")
            if cid in self.chats:
                sock.respond(frame, 200, self._page(self.chats[cid], cursor, limit))
            else:
                body = {
                    "userMessageModels": self.history,
                    "hasMore": 0,
                    "nextCursor": 1793378002814,
                }
                sock.respond(frame, 200, body)
        else:
            sock.respond(frame, 404, {"reason": "no such lwp"})

    @staticmethod
    def _page(models: list[dict], cursor: int, limit: int) -> dict:
        """实测的规则：从新到旧，游标是毫秒时间、包含等于它的；nextCursor 是下一条的时间。"""
        newest_first = sorted(models, key=lambda m: m["message"]["createAt"], reverse=True)
        rest = [m for m in newest_first if m["message"]["createAt"] <= cursor]
        page, after = rest[:limit], rest[limit:]
        return {
            "userMessageModels": page,
            "hasMore": int(bool(after)),
            "nextCursor": after[0]["message"]["createAt"] if after else None,
        }
