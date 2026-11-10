import asyncio
import base64
import json
import time
from urllib.parse import parse_qs

import httpx
import msgpack
import pytest
from fake_im import (
    FakeIm,
    chat_message,
    conversation,
    entry,
    history_message,
    read_receipt,
    session_activation,
    trade_card,
)

from xianyu_core import im
from xianyu_core.constants import IM_APP_KEY
from xianyu_core.credential import Credential
from xianyu_core.errors import (
    AuthRequiredError,
    ImError,
    ImRejectedError,
    ImSendUncertainError,
    RateLimitedError,
)
from xianyu_core.im import (
    AccessToken,
    ChatMessage,
    Conversation,
    ImClient,
    ImConnection,
    ImPush,
    MessageRead,
    decode_entry,
    fetch_access_token,
    new_device_id,
    reg_user_agent,
)
from xianyu_core.mtop import MtopClient

COOKIES = {"unb": "42", "cookie2": "c2"}
FAST = {"heartbeat_interval": 0.05, "response_timeout": 0.2, "ready_timeout": 0.5}


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 5))


async def open_conn(server: FakeIm, **kwargs) -> ImConnection:
    args = {**FAST, **kwargs}
    return await ImConnection.open(
        cookies=COOKIES, token="tok", device_id="DID-42", connect=server.connect, **args
    )


async def next_event(conn: ImConnection, events=None):
    events = events or conn.events()
    return await asyncio.wait_for(anext(events), 1)


# ---------- 小工具 ----------


def test_new_device_id_is_upper_uuid_with_unb():
    did = new_device_id("42")
    head, unb = did.rsplit("-", 1)
    assert unb == "42"
    assert head == head.upper() and len(head) == 36 and head[14] == "4"
    assert new_device_id("42") != did


def test_reg_user_agent_appends_dingweb():
    ua = reg_user_agent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    assert ua.endswith(
        "DingTalk(2.2.0) OS(Mac OS/10.15.7) Browser(Chrome/146.0.0.0) "
        "DingWeb/2.2.0 IMPaaS DingWeb/2.2.0"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_800_000_000_000, 1_800_000_000),  # 毫秒时间戳
        (1_800_000_000, 1_800_000_000),  # 秒级时间戳
        (7_200_000, 1_700_007_200),  # 剩余毫秒
        (7200, 1_700_007_200),  # 剩余秒
        ("1800000000000", 1_800_000_000),
        (None, None),
        ("abc", None),
        (0, None),
    ],
)
def test_expire_time(value, expected):
    assert im._expire_time(value, now=1_700_000_000) == expected


def test_decode_entry_json_and_msgpack():
    assert decode_entry(entry({"a": 1})["data"]) == {"a": 1}
    packed = base64.b64encode(msgpack.packb({1: {2: b"\xe4\xbd\xa0"}})).decode().rstrip("=")
    assert decode_entry(packed) == {"1": {"2": "你"}}


def test_fetch_access_token_sends_device_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen["api"] = request.url.params["api"]
        seen["data"] = json.loads(form["data"])
        data = {"accessToken": "tok", "accessTokenExpiredTime": "1800000000000"}
        return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": data})

    async def go():
        credential = Credential(cookies={"unb": "42", "_m_h5_tk": "tok_1"})
        async with MtopClient(credential, transport=httpx.MockTransport(handler)) as mtop:
            return await fetch_access_token(mtop, "DID-42")

    assert run(go()) == AccessToken("tok", 1_800_000_000)
    assert seen["api"] == im.TOKEN_API
    assert seen["data"] == {"appKey": IM_APP_KEY, "deviceId": "DID-42"}


# ---------- 解析 ----------


def test_chat_message_parse_and_scrub():
    data = decode_entry(entry(chat_message(), packer="msgpack")["data"])
    message = ChatMessage.parse(data)
    assert message is not None
    assert message.cid == "777"
    assert message.message_id == "1793378126001.PNM"
    assert message.sender_id == "200"
    assert message.sender_name == "买家小王"
    assert message.text == "还在吗"
    assert message.content_type == 1
    assert message.sent_at == 1793378126.437
    assert message.item_id == "300"
    assert message.session_type == "1"
    reminder = message.raw["1"]["10"]
    assert not im.SENDER_DEVICE_KEYS & reminder.keys()
    assert json.loads(reminder["extJson"]) == {"messageId": "1793378126001.PNM", "tag": "u"}
    # 原对象不动
    assert data["1"]["10"]["clientIp"] == "10.0.0.1"


def test_history_message_parses_like_push():
    pushed = ChatMessage.parse(decode_entry(entry(chat_message(), packer="msgpack")["data"]))
    model = history_message()
    message = ChatMessage.from_history(model)
    assert message is not None and pushed is not None
    fields = (
        "message_id",
        "cid",
        "sender_id",
        "sender_name",
        "sent_at",
        "content_type",
        "text",
        "session_type",
    )
    assert [getattr(message, f) for f in fields] == [getattr(pushed, f) for f in fields]
    assert message.item_id == "300"
    assert not im.SENDER_DEVICE_KEYS & message.raw["message"]["extension"].keys()
    assert model["message"]["extension"]["clientIp"] == "10.0.0.1"  # 原对象不动
    assert ChatMessage.from_history({"readStatus": 2}) is None


@pytest.mark.parametrize(
    ("notice", "content_type", "named"),
    [
        ("发来一条新消息", 1, True),  # 人打的字
        ("发来一条新消息", 2, True),  # 人发的图片
        ("～", 1, False),  # 自动回复："你有一条新消息"
        ("发来一条新消息", 14, False),  # 交易卡片，标题可能是"交易消息"
    ],
)
def test_sender_name_only_for_messages_from_the_person(notice, content_type, named):
    model = history_message(notice=notice)
    data = json.dumps({"contentType": content_type}).encode()
    model["message"]["content"]["custom"]["data"] = base64.b64encode(data).decode()
    message = ChatMessage.from_history(model)
    assert message is not None and message.content_type == content_type
    assert (message.sender_name != "") is named


IMAGE = {
    "contentType": 2,
    "image": {
        "pics": [
            {
                "url": "https://img.alicdn.com/imgextra/i3/x.jpg",
                "width": 884,
                "height": 1919,
                "type": 0,
            }
        ]
    },
}


def test_image_message_from_history_and_push():
    model = history_message(text="[图片]")
    data = base64.b64encode(json.dumps(IMAGE).encode()).decode()
    model["message"]["content"]["custom"].update(type=2, data=data)
    pushed = chat_message(text="[图片]")
    pushed[1][6][3].update({4: 2, 5: json.dumps(IMAGE)})
    for message in (
        ChatMessage.from_history(model),
        ChatMessage.parse(decode_entry(entry(pushed, packer="msgpack")["data"])),
    ):
        assert message is not None and message.content_type == 2
        url = IMAGE["image"]["pics"][0]["url"]
        assert message.images == [im.ChatImage(url, 884, 1919)]
        assert message.text == "[图片]"
    assert ChatMessage.from_history(history_message()).images == []


def test_trade_card_gives_order_and_role_from_history_and_push():
    pushed = decode_entry(entry(trade_card(push=True), packer="msgpack")["data"])
    for message in (ChatMessage.from_history(trade_card()), ChatMessage.parse(pushed)):
        assert message is not None and message.content_type == 26
        assert (message.order_id, message.order_role) == ("5127299211174026539", "seller")
        assert message.text == "[我已付款，等待你发货]"
    # 链接里的角色大小写不定
    bought = ChatMessage.from_history(trade_card(order_id="5127546926073005319", role="Buyer"))
    assert (bought.order_id, bought.order_role) == ("5127546926073005319", "buyer")
    assert ChatMessage.from_history(history_message()).order_id == ""


def card_message(content_type: int, card: dict, task: str = "") -> ChatMessage:
    model = history_message(text="[卡片]")
    data = base64.b64encode(json.dumps({"contentType": content_type, **card}).encode()).decode()
    model["message"]["content"]["custom"].update(type=content_type, data=data)
    if task:
        model["message"]["extension"]["bizTag"] = json.dumps({"taskName": task})
    message = ChatMessage.from_history(model)
    assert message is not None
    return message


@pytest.mark.parametrize(
    ("content_type", "card", "task", "expected"),
    [
        # 交易提示"[你已发货]"不带订单号
        (14, {"tip": {"tip": "你已发货"}}, "已发货_卖家", ("", "")),
        # 退款卡片：链接里是 orderId，角色看 taskName
        (
            26,
            {
                "dxCard": {
                    "item": {
                        "main": {
                            "targetUrl": "https://h5.m.goofish.com/refundDetail"
                            "?kun=true&orderId=5127816672001096520"
                        }
                    }
                }
            },
            "发起退款申请_买家",
            ("5127816672001096520", "buyer"),
        ),
        # 有的提示直接带 orderId 字段，没有角色
        (
            14,
            {"tip": {"argInfo": {"args": {"orderId": "5127298851039014501"}}}},
            "神奇鱼塘-蚂蚁森林能量",
            ("5127298851039014501", ""),
        ),
        # 物流卡片：链接在 HTML 里
        (
            6,
            {
                "textCard": {
                    "content": '3天后自动确认收货 <a href="fleamarket://order_detail'
                    '?id=5127801553318005319&role=Buyer">查看详情</a>'
                }
            },
            "签收缩短收货",
            ("5127801553318005319", "buyer"),
        ),
        # 链接里没写角色，看 taskName
        (
            26,
            {
                "dxCard": {
                    "item": {
                        "main": {"targetUrl": "fleamarket://order_detail?id=5127299211174026539"}
                    }
                }
            },
            "已拍下_未付款_卖家",
            ("5127299211174026539", "seller"),
        ),
    ],
)
def test_order_ref_in_other_cards(content_type, card, task, expected):
    message = card_message(content_type, card, task)
    assert (message.order_id, message.order_role) == expected


def test_order_link_typed_by_a_person_is_not_an_order():
    text = "fleamarket://order_detail?id=5127299211174026539&role=seller"
    assert ChatMessage.from_history(history_message(text=text)).order_id == ""


@pytest.mark.parametrize("unread", [0, 1, None])
def test_unread_count_from_history_and_push(unread):
    """平台的 unreadCount：推送里是 1.8，历史消息里是 message.unreadCount。没带是 None。"""
    pushed = ChatMessage.parse(
        decode_entry(entry(chat_message(sender="42", unread=unread), packer="msgpack")["data"])
    )
    history = ChatMessage.from_history(history_message(sender="42", unread=unread))
    assert pushed is not None and history is not None
    assert pushed.unread_count == history.unread_count == unread


def test_read_receipt_parse():
    data = decode_entry(read_receipt("1793378200001.PNM", cid="777", read_ms=1793378300500)["data"])
    assert MessageRead.parse(data) == MessageRead(
        message_id="1793378200001.PNM", cid="777", unread_count=0, read_at=1793378300.5
    )
    # 没带还有几个没读的：回执本身就是有人读了，按 0 算
    assert MessageRead.parse({"1": "x.PNM"}) == MessageRead("x.PNM", "", 0, None)
    assert MessageRead.parse({"1": "x.PNM", "3": "?"}) is None  # 带了但看不懂
    assert MessageRead.parse({"3": 0}) is None  # 没说是哪条消息
    assert MessageRead.parse({"1": ["x.PNM"], "3": 0}) is None
    assert MessageRead.parse(["x"]) is None


def test_chat_message_parse_ignores_other_entries():
    assert ChatMessage.parse(session_activation()) is None
    assert ChatMessage.parse({"1": "x"}) is None
    assert ChatMessage.parse([1, 2]) is None


def test_conversation_parse_finds_peer():
    conv = Conversation.parse(conversation("777", "42", "200", unread=2), "42")
    assert conv is not None
    assert (conv.cid, conv.peer_id, conv.peer_name) == ("777", "200", "买家小王")
    assert (conv.item_id, conv.item_title, conv.biz_type, conv.unread) == (
        "300",
        "九成新洗衣机",
        "1",
        2,
    )
    assert conv.updated_at == 1793378003.908 and conv.peer_type == "0"
    assert Conversation.parse({"type": 2}, "42") is None


@pytest.mark.parametrize("last_from", ["me", None])
def test_conversation_peer_name_only_from_peer_text(last_from):
    """最后一条是自己发的、或者没有最后一条时不知道昵称；squadName 是商品简称，不能当昵称。"""
    conv = Conversation.parse(conversation("777", "42", "200", last_from=last_from), "42")
    assert conv is not None and conv.peer_name == ""
    # 自己是 owner 时，对方的用户类型在 ext 那边
    row = conversation("777", "42", "200")
    ext = row["singleChatUserConversation"]["singleChatConversation"]["extension"]
    ext.update(ownerUserId="42", ownerUserType="0", extUserId="200", extUserType="3")
    swapped = Conversation.parse(row, "42")
    assert swapped is not None and swapped.peer_type == "3"


def test_fetch_peer_nick_asks_like_the_web_page(platform):
    platform.nicks["777"] = "山里的松鼠"

    async def go():
        credential = Credential(cookies={"unb": "42", "_m_h5_tk": "tok_1"})
        async with MtopClient(credential, transport=httpx.MockTransport(platform)) as mtop:
            named = await im.fetch_peer_nick(mtop, "777", session_type="1", peer_type="0")
            masked = await im.fetch_peer_nick(mtop, "778", session_type="1", peer_type="")
        return named, masked

    assert run(go()) == ("山里的松鼠", "x***9")  # 没有闲鱼昵称时用打了码的账号名
    assert platform.user_queries == [
        {"type": 0, "sessionType": 1, "sessionId": "777", "isOwner": False},
        {"type": 0, "sessionType": 1, "sessionId": "778", "isOwner": False},
    ]


def test_conversation_visible():
    row = conversation("777", "42", "200")
    assert Conversation.parse(row, "42").visible
    row["singleChatUserConversation"]["visible"] = 0  # 在 App 里删掉的会话
    assert not Conversation.parse(row, "42").visible
    del row["singleChatUserConversation"]["visible"]
    assert Conversation.parse(row, "42").visible
    assert not Conversation.parse(row, "42").muted
    row["singleChatUserConversation"]["muteNotification"] = 1  # 消息免打扰
    assert Conversation.parse(row, "42").muted


@pytest.mark.parametrize(
    ("biz_type", "peer", "with_user"),
    [
        ("1", "200", True),  # 商品会话
        ("15", "200", True),  # 网页版也显示的别的聊天类型
        ("3", "0", False),  # 通知消息（活动推广）
        ("24", "0", False),  # 闲小蜜
        ("66", "201", False),  # 网页版不显示的类型，哪怕对方是个账号
        ("1", "0", False),  # 对方是系统
    ],
)
def test_conversation_with_user(biz_type, peer, with_user):
    conv = Conversation.parse(conversation("777", "42", peer, biz_type=biz_type), "42")
    assert conv is not None and conv.with_user is with_user


# ---------- 一条连接 ----------


def test_open_registers_and_waits_ready():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        await asyncio.sleep(0.02)  # 等对齐同步位点的两个请求
        await conn.close()

    run(go())
    sock = server.last
    assert sock.kwargs["url"] == im.IM_WS_URL
    assert sock.kwargs["additional_headers"]["Cookie"] == "unb=42; cookie2=c2"
    assert sock.kwargs["additional_headers"]["Origin"] == "https://www.goofish.com"
    assert sock.kwargs["ping_interval"] is None
    reg = sock.requests("/reg")[0]["headers"]
    assert (reg["token"], reg["did"], reg["app-key"]) == ("tok", "DID-42", IM_APP_KEY)
    assert reg["sync"] == "0,0;0;0;" and "IMPaaS" in reg["ua"]
    # /s/sync 带 syncExtraType 2：getState 再 ackDiff，ackDiff 带着 getState 返回的 body
    assert sock.requests("/r/SyncStatus/ackDiff")[0]["body"] == [{"topic": "sync", "pts": 1}]
    # 两帧推送各回一次 ACK，原样带着服务端的 headers
    assert [ack["code"] for ack in sock.acks] == [200, 200]
    assert sock.acks[1]["headers"]["sid"] == "s1"
    assert sock.closed


def test_open_returns_as_soon_as_ready():
    server = FakeIm()

    async def go():
        started = time.monotonic()
        conn = await open_conn(server, ready_timeout=3)
        elapsed = time.monotonic() - started
        await conn.close()
        return elapsed

    assert run(go()) < 0.5


def test_kickout_before_ready_fails_open_at_once():
    server = FakeIm()
    server.ready = False

    async def go():
        task = asyncio.create_task(open_conn(server, ready_timeout=3))
        await asyncio.sleep(0.05)
        server.last.push("/push/kickout")
        started = time.monotonic()
        with pytest.raises(ImRejectedError):
            await task
        return time.monotonic() - started

    assert run(go()) < 0.5


def test_events_yield_pushes_and_chat_messages():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        events = conn.events()
        first = await next_event(conn, events)
        messages = [chat_message(), chat_message(text="多少钱")]
        server.last.push_entries([entry(m, packer="msgpack") for m in messages])
        second = await next_event(conn, events)
        third = await next_event(conn, events)
        await conn.close()
        return first, second, third

    first, second, third = run(go())
    assert isinstance(first, ImPush) and first.lwp == "/s/vulcan"
    assert isinstance(second, ChatMessage) and second.text == "还在吗"
    assert isinstance(third, ChatMessage) and third.text == "多少钱"
    assert len(server.last.acks) == 3  # 一帧两条也只回一次 ACK


def test_events_yield_read_receipts():
    """条目类型 40103 是已读回执；解不出来的照样给出来，带着条目类型。"""
    server = FakeIm()
    broken = entry({3: 0}, packer="msgpack", object_type=im.READ_RECEIPT_OBJECT_TYPE)

    async def go():
        conn = await open_conn(server)
        events = conn.events()
        await next_event(conn, events)  # /s/vulcan
        server.last.push_entries([read_receipt("m2", cid="777", unread=0), broken])
        receipt = await next_event(conn, events)
        other = await next_event(conn, events)
        await conn.close()
        return receipt, other

    receipt, other = run(go())
    assert receipt == MessageRead(message_id="m2", cid="777", unread_count=0, read_at=0.0)
    assert isinstance(other, ImPush) and other.object_type == im.READ_RECEIPT_OBJECT_TYPE


def test_reg_invalid_token_is_rejected():
    server = FakeIm()
    server.reg_code = 401
    server.reg_body = {"reason": "invalid token"}

    with pytest.raises(ImRejectedError) as exc:
        run(open_conn(server))
    assert exc.value.reason == ImRejectedError.INVALID_TOKEN
    assert server.last.closed


def test_reg_device_mismatch_is_invalid_token():
    server = FakeIm()
    server.reg_code = 400
    server.reg_body = {"reason": "device id or appkey is not equal"}

    with pytest.raises(ImRejectedError) as exc:
        run(open_conn(server))
    assert exc.value.reason == ImRejectedError.INVALID_TOKEN


def test_reg_other_error_is_plain_im_error():
    server = FakeIm()
    server.reg_code = 500

    with pytest.raises(ImError) as exc:
        run(open_conn(server))
    assert not isinstance(exc.value, ImRejectedError)
    assert exc.value.code == 500


def test_not_ready_in_time():
    server = FakeIm()
    server.ready = False

    with pytest.raises(ImError, match="vulcan"):
        run(open_conn(server, ready_timeout=0.1))
    assert server.last.closed


def test_heartbeat_without_answer_ends_connection():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        events = conn.events()
        await next_event(conn, events)  # /s/vulcan 的条目
        server.heartbeat_ok = False
        with pytest.raises(ImError, match="心跳"):
            await next_event(conn, events)
        await conn.close()

    run(go())
    assert server.last.requests("/!")
    assert server.last.closed


def test_kickout_ends_connection():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        events = conn.events()
        await next_event(conn, events)
        server.last.push("/push/kickout")
        with pytest.raises(ImRejectedError) as exc:
            await next_event(conn, events)
        await asyncio.sleep(0.01)
        await conn.close()
        return exc.value

    assert run(go()).reason == ImRejectedError.KICKOUT
    assert server.last.closed


def test_server_drop_ends_events_and_requests():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        events = conn.events()
        await next_event(conn, events)
        server.last.drop()
        with pytest.raises(ImError, match="连接断了"):
            await next_event(conn, events)
        with pytest.raises(ImError):
            await conn.request("/!")
        await conn.close()

    run(go())


def test_list_conversations():
    server = FakeIm()
    server.conversations = [conversation("777", "42", "200", unread=1), {"type": 9}]

    async def go():
        conn = await open_conn(server)
        page = await conn.list_conversations("42", limit=10)
        await conn.close()
        return page

    page = run(go())
    assert [c.cid for c in page.conversations] == ["777"]
    assert page.has_more and page.next_cursor == 1790449136584
    assert server.last.requests("/r/Conversation/listNewestPagination")[0]["body"] == [
        im.NEWEST_CURSOR,
        10,
    ]


def test_list_messages():
    server = FakeIm()
    server.history = [history_message(text="多少钱"), {"message": "坏的"}]

    async def go():
        conn = await open_conn(server)
        page = await conn.list_messages("777", limit=5)
        await conn.close()
        return page

    page = run(go())
    assert [m.text for m in page.messages] == ["多少钱"]
    assert not page.has_more and page.next_cursor == 1793378002814
    assert server.last.requests("/r/MessageManager/listUserMessages")[0]["body"] == [
        "777@goofish",
        False,
        im.NEWEST_CURSOR,
        5,
        False,
    ]


def test_send_text_uses_web_format_and_returns_the_message():
    server = FakeIm()
    server.send_echo = None

    async def go():
        conn = await open_conn(server)
        message = await conn.send_text("777", "在的，今天能发货", me="42", peer="200")
        await conn.close()
        return message

    message = run(go())
    (frame,) = server.last.requests(im.SEND_API)
    model, receivers = frame["body"]
    data = base64.b64decode(model.pop("content")["custom"].pop("data")).decode()
    # 和网页版的 JSON.stringify 一样：没有空格，中文不转义
    assert data == '{"contentType":1,"text":{"text":"在的，今天能发货"}}'
    assert model.pop("uuid").startswith("-")
    assert model == {
        "cid": "777@goofish",
        "conversationType": 1,
        "redPointPolicy": 0,
        "extension": {"extJson": "{}"},
        "ctx": {"appVersion": "1.0", "platform": "web"},
        "mtags": {},
        "msgReadStatusSetting": 1,
    }
    assert receivers == {"actualReceivers": ["200@goofish", "42@goofish"]}
    assert (message.message_id, message.cid, message.sender_id) == (
        "1793378200001.PNM",
        "777",
        "42",
    )
    assert (message.text, message.content_type, message.sent_at) == (
        "在的，今天能发货",
        1,
        1793378201.0,
    )
    assert message.unread_count == 1  # 刚发出去，对方还没读


def test_clear_red_point_like_the_web_page():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        await conn.clear_red_point("777", "1793378126001.PNM")
        await conn.close()

    run(go())
    (frame,) = server.last.requests(im.CLEAR_RED_POINT_API)
    assert frame["body"] == [[{"messageId": "1793378126001.PNM", "cid": "777@goofish"}]]


def test_new_uuid_counts_up():
    first, second = im.new_uuid(), im.new_uuid()
    assert first.startswith("-") and first != second


@pytest.mark.parametrize(
    ("reply", "error", "text"),
    [
        ("reject", ImError, "绑定支付宝"),
        ("silent", ImSendUncertainError, "不知道消息发出去没有"),
        ("no_id", ImSendUncertainError, "没给消息 ID"),
    ],
)
def test_send_text_failures(reply, error, text):
    server = FakeIm()
    server.send_reply = reply

    async def go():
        conn = await open_conn(server)
        try:
            await conn.send_text("777", "好的", me="42", peer="200")
        finally:
            await conn.close()

    with pytest.raises(error, match=text) as exc:
        run(go())
    if reply == "reject":
        assert not isinstance(exc.value, ImSendUncertainError) and exc.value.code == 400


def test_send_text_on_closed_connection_is_not_sent():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        server.last.drop()
        await asyncio.wait_for(conn._ended.wait(), 1)
        try:
            await conn.send_text("777", "好的", me="42", peer="200")
        finally:
            await conn.close()

    with pytest.raises(ImError, match="没有发出去") as exc:
        run(go())
    assert not isinstance(exc.value, ImSendUncertainError)
    assert server.sends == 0


def test_request_error_code():
    server = FakeIm()

    async def go():
        conn = await open_conn(server)
        try:
            await conn.request("/r/Unknown/thing", [])
        finally:
            await conn.close()

    with pytest.raises(ImError, match="no such lwp") as exc:
        run(go())
    assert exc.value.code == 404


# ---------- 长期在线 ----------


class Tokens:
    """假的取 token：每次给一个新的，`errors` 里放要依次抛的异常。"""

    def __init__(self, *errors, expires_at=None):
        self.errors = list(errors)
        self.expires_at = expires_at
        self.count = 0

    async def __call__(self) -> AccessToken:
        if self.errors:
            raise self.errors.pop(0)
        self.count += 1
        return AccessToken(f"tok{self.count}", self.expires_at)


def make_client(server: FakeIm, tokens: Tokens, **kwargs) -> ImClient:
    args = {
        **FAST,
        "reconnect_delays": (0.01, 0.02),
        "stable_after": 0.3,
        **kwargs,
    }
    return ImClient(
        device_id="DID-42",
        fetch_token=tokens,
        load_cookies=lambda: COOKIES,
        connect=server.connect,
        **args,
    )


async def wait_until(check, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.005)


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_client_delivers_events_and_reconnects_with_fresh_token():
    server = FakeIm()
    tokens = Tokens()
    client = make_client(server, tokens)
    events, connected = [], []

    async def on_event(conn, event):
        events.append(event)

    async def on_connected(conn):
        connected.append(conn)

    async def go():
        task = asyncio.create_task(client.run(on_event, on_connected))
        await wait_until(lambda: client.status.state == "online" and events)
        server.last.push_entries([entry(chat_message(), packer="msgpack")])
        await wait_until(lambda: len(events) == 2)
        server.last.drop()
        await wait_until(lambda: len(connected) == 2 and client.status.state == "online")
        await stop(task)

    run(go())
    assert isinstance(events[1], ChatMessage)
    assert [s.requests("/reg")[0]["headers"]["token"] for s in server.sockets] == ["tok1", "tok2"]
    assert client.status.state == "stopped"
    assert all(s.closed for s in server.sockets)


def test_client_backs_off_when_cannot_connect():
    server = FakeIm()
    server.refuse = 2
    client = make_client(server, Tokens())

    async def go():
        task = asyncio.create_task(client.run(_ignore))
        await wait_until(lambda: client.status.state == "online")
        await stop(task)

    run(go())
    assert len(server.sockets) == 1


def test_client_retries_token_errors_and_rate_limit():
    from xianyu_core.errors import MtopError

    server = FakeIm()
    tokens = Tokens(MtopError("平台出错"), RateLimitedError("太快了", retry_after=0.01))
    client = make_client(server, tokens)

    async def go():
        task = asyncio.create_task(client.run(_ignore))
        await wait_until(lambda: client.status.state == "online")
        await stop(task)

    run(go())
    assert tokens.count == 1


def test_client_stops_when_login_expired():
    client = make_client(FakeIm(), Tokens(AuthRequiredError("登录失效")))

    with pytest.raises(AuthRequiredError):
        run(client.run(_ignore))
    assert client.status.state == "stopped" and "登录失效" in client.status.message


def test_client_retries_invalid_token_once():
    server = FakeIm()
    server.reg_code = 401

    client = make_client(server, Tokens())
    with pytest.raises(ImRejectedError) as exc:
        run(client.run(_ignore))
    assert exc.value.reason == ImRejectedError.INVALID_TOKEN
    assert len(server.sockets) == 2  # 换了一个 token 又试了一次


def test_client_reconnects_after_kickout_but_gives_up_after_three():
    server = FakeIm()
    client = make_client(server, Tokens())

    async def go():
        task = asyncio.create_task(client.run(_ignore))
        for n in range(1, 4):
            await wait_until(
                lambda n=n: len(server.sockets) == n and client.status.state == "online"
            )
            server.last.push("/push/kickout")
        with pytest.raises(ImRejectedError) as exc:
            await task
        return exc.value

    assert run(go()).reason == ImRejectedError.KICKOUT
    assert len(server.sockets) == 3


def test_client_stops_when_session_removed():
    server = FakeIm()
    client = make_client(server, Tokens())

    async def go():
        task = asyncio.create_task(client.run(_ignore))
        await wait_until(lambda: client.status.state == "online")
        server.last.push("/s/session/remove")
        with pytest.raises(ImRejectedError) as exc:
            await task
        return exc.value

    assert run(go()).reason == ImRejectedError.SESSION_REMOVED
    assert client.status.state == "stopped"


def test_client_rotates_token_before_expiry(monkeypatch):
    monkeypatch.setattr(im, "TOKEN_ROTATE_LEAD", 0)
    monkeypatch.setattr(im, "MIN_ROTATE_AFTER", 0)
    server = FakeIm()
    tokens = Tokens()
    client = make_client(server, tokens)

    async def go():
        tokens.expires_at = time.time() + 0.1
        task = asyncio.create_task(client.run(_ignore))
        await wait_until(lambda: len(server.sockets) >= 2)
        await stop(task)

    run(go())
    assert tokens.count >= 2


def test_client_survives_handler_errors():
    server = FakeIm()
    client = make_client(server, Tokens())
    seen = []

    async def on_event(conn, event):
        seen.append(event)
        raise RuntimeError("业务层出错")

    async def go():
        task = asyncio.create_task(client.run(on_event))
        await wait_until(lambda: len(seen) == 1)
        server.last.push_entries([entry(chat_message(), packer="msgpack")])
        await wait_until(lambda: len(seen) == 2)
        await stop(task)

    run(go())
    assert len(server.sockets) == 1


async def _ignore(conn, event):
    pass
