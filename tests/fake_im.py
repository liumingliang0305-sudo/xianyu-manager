"""假的即时消息服务端，换掉 websockets.connect 用。"""

import asyncio
import base64
import json

import msgpack
import websockets

PUSH_HEADERS = {"app-key": "k", "ua": "server", "sid": "s1"}


def entry(data, *, packer: str = "json", object_type: int = 40000) -> dict:
    """推送里的一个条目：data 按 JSON 或 MessagePack 打包再 base64。"""
    raw = msgpack.packb(data) if packer == "msgpack" else json.dumps(data).encode()
    return {"data": base64.b64encode(raw).decode(), "objectType": object_type}


def read_receipt(message_id: str, *, cid: str = "777", unread: int = 0, read_ms: int = 0) -> dict:
    """已读回执的推送条目（类型 40103）：自己发的这条消息还有几个接收方没读，字段照网页版的
    MsgReadToSenderEventModel。"""
    data = {1: message_id, 2: 2, 3: unread, 4: f"{cid}@goofish", 5: 1, 6: read_ms}
    return entry(data, packer="msgpack", object_type=40103)


def chat_message(
    *,
    cid: str = "777",
    sender: str = "200",
    text: str = "还在吗",
    item: str = "300",
    message_id: str = "1793378126001.PNM",
    sent_ms: int = 1793378126437,
    session_type: str = "1",
    notice: str = "发来一条新消息",
    unread: int | None = None,
) -> dict:
    """照实测的格式造一条聊天消息（MessagePack 解出来的那种，键是整数）。

    人发的消息 `notice` 是"发来一条新消息"，提醒标题是昵称；自动回复是"～"，标题是"你有一条新消息"。
    """
    inner = json.dumps({"contentType": 1, "text": {"text": text}, "atUsers": []})
    ext = {"messageId": message_id, "umidToken": "secret", "utdid": "secret", "tag": "u"}
    body = {
        1: {
            1: {1: f"{sender}@goofish"},
            2: f"{cid}@goofish",
            3: message_id,
            5: sent_ms,
            6: {1: 101, 3: {1: "", 2: text, 3: "", 4: 1, 5: inner}},
            10: {
                "senderUserId": sender,
                "reminderTitle": "买家小王" if notice == "发来一条新消息" else "你有一条新消息",
                "reminderNotice": notice,
                "reminderContent": text,
                "reminderUrl": (
                    f"fleamarket://message_chat?itemId={item}&peerUserId={sender}"
                    f"&sid={cid}&messageId={message_id}&adv=no"
                ),
                "sessionType": session_type,
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
    if unread is not None:
        body[1].update({7: 2, 8: unread})  # 接收方个数、还有几个没读
    return body


def history_message(
    *,
    cid: str = "777",
    sender: str = "200",
    text: str = "还在吗",
    item: str = "300",
    message_id: str = "1793378126001.PNM",
    sent_ms: int = 1793378126437,
    session_type: str = "1",
    notice: str = "发来一条新消息",
    unread: int | None = None,
) -> dict:
    """照实测的格式造一条历史消息（listUserMessages 的 userMessageModels 里的一条）。
    `unread` 是平台的 unreadCount（还有几个接收方没读），None 时不带。"""
    data = json.dumps({"contentType": 1, "text": {"text": text}, "atUsers": []})
    reminder = chat_message(
        cid=cid,
        sender=sender,
        text=text,
        item=item,
        message_id=message_id,
        session_type=session_type,
        notice=notice,
    )[1][10]
    model = {
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
    if unread is not None:
        model["message"].update(receiverCount=2, unreadCount=unread)
    return model


def trade_card(
    *,
    order_id: str = "5127299211174026539",
    role: str = "seller",
    title: str = "我已付款，等待你发货",
    cid: str = "777",
    sender: str = "200",
    message_id: str = "1793378127001.PNM",
    sent_ms: int = 1793378127437,
    push: bool = False,
) -> dict:
    """照实测的格式造一张交易卡片（内容类型 26）：订单详情链接里的 role 是当前账号的角色，
    提醒里 bizTag 的 taskName 以"_卖家"或"_买家"结尾。`push` 时是推送的格式，否则是历史消息。"""
    card = {
        "contentType": 26,
        "dxCard": {
            "item": {
                "main": {
                    "exContent": {"title": title, "desc": "请双方沟通及时确认价格"},
                    "targetUrl": f"fleamarket://order_detail?id={order_id}&role={role}",
                }
            }
        },
    }
    side = "卖家" if role.lower() == "seller" else "买家"
    task = json.dumps({"taskName": f"付款完成待发货_{side}", "taskId": "t1"}, ensure_ascii=False)
    summary = f"[{title}]"
    if push:
        message = chat_message(
            cid=cid, sender=sender, text=summary, message_id=message_id, sent_ms=sent_ms
        )
        message[1][6][3].update({2: summary, 4: 26, 5: json.dumps(card)})
        message[1][10]["bizTag"] = task
        return message
    message = history_message(
        cid=cid, sender=sender, text=summary, message_id=message_id, sent_ms=sent_ms
    )
    message["message"]["content"]["custom"].update(
        type=26, summary=summary, data=base64.b64encode(json.dumps(card).encode()).decode()
    )
    message["message"]["extension"]["bizTag"] = task
    return message


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
    biz_type: str = "1",
    last_from: str | None = "peer",
    last_unread: int | None = None,
) -> dict:
    """照实测的格式造会话列表里的一个会话。squadName 是商品的简称，不是昵称；对方昵称只能从
    最后一条消息看出来。`last_from`："peer" 最后一条是对方发的文本，"me" 是自己发的，
    None 没有最后一条。"""
    row = {
        "type": 1,
        "singleChatUserConversation": {
            "redPoint": unread,
            "modifyTime": modified_ms,
            "visible": 1,
            "singleChatConversation": {
                "cid": f"{cid}@goofish",
                "pairFirst": f"{peer}@goofish",
                "pairSecond": f"{me}@goofish",
                "bizType": biz_type,
                "extension": {
                    "itemId": item,
                    "itemTitle": "九成新洗衣机",
                    "ownerUserId": peer,
                    "ownerUserType": "10" if peer == "0" else "0",
                    "extUserId": me,
                    "extUserType": "0",
                    f"squadName_{peer}": "洗衣机",
                    f"squadName_{me}": "洗衣机",
                },
            },
        },
    }
    if last_from:
        sender = peer if last_from == "peer" else me
        last = history_message(
            cid=cid, sender=sender, session_type=biz_type, sent_ms=modified_ms, unread=last_unread
        )
        row["singleChatUserConversation"]["lastMessage"] = last
    return row


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
        # 发消息怎么应答：ok 收下；reject 拒绝（要绑支付宝）；silent 不回应；
        # no_id 回 200 但没有消息 ID
        self.send_reply = "ok"
        # 收下以后把这条消息推回来：after 在响应之后推，before 在响应之前推，None 不推。
        # 实测平台不推回发它的连接，默认不推；推回来的情况留着测去重
        self.send_echo: str | None = None
        self.sends = 0
        self.send_base_ms = 1793378200000  # 第 n 条发出去的消息时间是它加 n 秒
        self.cleared: list[dict] = []  # 清红点请求里的 {messageId, cid}

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
        elif lwp == "/r/MessageSend/sendByReceiverScope":
            self._send(sock, frame)
        elif lwp == "/r/Conversation/clearRedPoint":
            self.cleared += frame["body"][0]
            sock.respond(frame, 200)
        else:
            sock.respond(frame, 404, {"reason": "no such lwp"})

    def _send(self, sock: FakeSocket, frame: dict) -> None:
        self.sends += 1
        model, receivers = frame["body"]
        if self.send_reply == "silent":
            return
        if self.send_reply == "reject":
            sock.respond(frame, 400, {"reason": "send fail", "moreInfo": "USER_NEED_ALIPYA_BIND"})
            return
        if self.send_reply == "no_id":
            sock.respond(frame, 200, {"uuid": model["uuid"]})
            return
        message_id = f"{1793378200000 + self.sends}.PNM"
        created = self.send_base_ms + self.sends * 1000
        inner = json.loads(base64.b64decode(model["content"]["custom"]["data"]))
        me = receivers["actualReceivers"][1].removesuffix("@goofish")
        echo = chat_message(
            cid=model["cid"].removesuffix("@goofish"),
            sender=me,
            text=inner["text"]["text"],
            message_id=message_id,
            sent_ms=created,
        )
        if self.send_echo == "before":
            sock.push_entries([entry(echo, packer="msgpack")])
        body = {
            "messageId": message_id,
            "uuid": model["uuid"],
            "createAt": created,
            "receiverCount": 2,
            "unreadCount": 1,
            "extension": {"senderUserId": me, "reminderContent": inner["text"]["text"]},
        }
        sock.respond(frame, 200, body)
        if self.send_echo == "after":
            sock.push_entries([entry(echo, packer="msgpack")])

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
