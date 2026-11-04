import asyncio
import base64
import json
import os
import time
from collections import defaultdict

import httpx
import pytest
from fake_im import FakeIm, chat_message, conversation, entry, history_message, trade_card
from fakes import UNB, add_user, sign_in
from fastapi.testclient import TestClient

from app import db, messages
from app.api import create_app, sse_events
from app.messages import ImLock, ImOfflineError, MessageHub, MessageStore
from xianyu_core.credential import Credential
from xianyu_core.errors import ImSendUncertainError, RateLimitedError
from xianyu_core.guard import SEND, Guard
from xianyu_core.im import ChatImage, ChatMessage, Conversation, ImConnection, decode_entry

T = 1793378000000  # 毫秒
FAST = {"heartbeat_interval": 0.05, "response_timeout": 0.5, "ready_timeout": 0.5}
IM_OPTIONS = {
    "client_options": {**FAST, "reconnect_delays": (0.01, 0.02), "stable_after": 0.3},
    "watch_interval": 0.02,
    "request_interval": 0,
}


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 5))


async def wait_until(check, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.005)


@pytest.fixture
def store(database) -> MessageStore:
    return MessageStore(database)


def make_hub(store: MessageStore, accounts, server: FakeIm) -> MessageHub:
    return MessageHub(
        store, accounts, defaultdict(asyncio.Lock), connect=server.connect, **IM_OPTIONS
    )


def conv(cid: str, peer: str, *, unread: int = 0, modified_ms: int = T) -> Conversation:
    parsed = Conversation.parse(
        conversation(cid, UNB, peer, unread=unread, modified_ms=modified_ms), UNB
    )
    assert parsed
    return parsed


def chat(n: int, *, cid: str = "777", sender: str = "200") -> dict:
    """第 n 条历史消息，时间 T + n 秒。"""
    return history_message(
        cid=cid, sender=sender, text=f"第{n}条", message_id=f"m{n}", sent_ms=T + n * 1000
    )


def live(n: int, *, cid: str = "777", sender: str = "200") -> ChatMessage:
    message = ChatMessage.from_history(chat(n, cid=cid, sender=sender))
    assert message
    return message


# ---------- 本地库 ----------


def test_store_conversations_and_messages(store):
    store.save_conversations(UNB, [conv("777", "200", unread=2, modified_ms=T + 5000)])
    store.save_conversations(UNB, [conv("778", "201", modified_ms=T + 9000)])
    assert [c.cid for c in store.conversations(UNB)] == ["778", "777"]

    c = store.conversation(UNB, "777")
    assert (c.peer_id, c.peer_name, c.item_title, c.unread) == (
        "200",
        "买家小王",
        "九成新洗衣机",
        2,
    )
    assert c.stale and c.older_more is None and c.last_text == ""

    new = store.save_messages(UNB, [live(1), live(2, sender=UNB), live(3)])
    assert [m.message_id for m in new] == ["m1", "m2", "m3"]
    assert store.save_messages(UNB, [live(3), live(4)]) == [live(4)]

    records, more = store.messages(UNB, "777", limit=3)
    assert [r.message_id for r in records] == ["m2", "m3", "m4"]
    assert more
    assert [r.mine for r in records] == [True, False, False]
    assert store.conversation(UNB, "777").last_text == "第4条"

    store.mark_fetched(UNB, "777")
    assert not store.conversation(UNB, "777").stale
    # 平台上又有动静了
    store.save_conversations(UNB, [conv("777", "200", unread=0, modified_ms=T + 7000)])
    c = store.conversation(UNB, "777")
    assert c.stale and c.unread == 0
    assert store.stale(UNB, ["778", "777", "999"]) == ["778", "777"]


def test_store_live_message_updates_conversation(store):
    store.save_conversations(UNB, [conv("777", "200", unread=1, modified_ms=T)])
    store.mark_fetched(UNB, "777")

    assert store.add_live(UNB, live(5))
    assert not store.add_live(UNB, live(5))  # 重复推送
    c = store.conversation(UNB, "777")
    assert c.unread == 2 and c.updated_at == T / 1000 + 5
    assert not c.stale  # 原来跟上了，收到推送还是跟上的

    assert store.add_live(UNB, live(6, sender=UNB))
    assert store.conversation(UNB, "777").unread == 0

    # 本地没有的会话：先建一个，等补拉
    assert store.add_live(UNB, live(1, cid="900", sender="300"))
    c = store.conversation(UNB, "900")
    assert (c.peer_id, c.peer_name, c.item_id, c.unread) == ("300", "买家小王", "300", 1)
    assert c.stale


def test_store_peer_name_falls_back_to_messages_from_the_person(store):
    nameless = conversation("777", UNB, "200", last_from="me")
    store.save_conversations(UNB, [Conversation.parse(nameless, UNB)])
    assert store.conversation(UNB, "777").peer_name == ""

    # 自动回复的提醒标题是"你有一条新消息"，不是昵称
    auto = ChatMessage.from_history(history_message(message_id="a1", notice="～"))
    assert auto is not None and auto.sender_name == ""
    store.save_messages(UNB, [auto])
    assert store.conversation(UNB, "777").peer_name == ""
    assert store.nameless(UNB, ["777"]) == ["777"]
    store.save_messages(UNB, [live(2)])
    assert store.conversation(UNB, "777").peer_name == "买家小王"
    assert store.nameless(UNB, ["777"]) == []


def test_store_forgets_names_that_are_not_nicknames(store):
    """以前存的消息，sender_name 直接取的提醒标题；卡片、自动回复的那种清掉，人发的留着。"""
    store.save_conversations(UNB, [conv("777", "200")])
    shipped, card, person = live(1), live(2), live(3)
    shipped.sender_name = "交易消息"
    shipped.raw["message"]["extension"]["reminderNotice"] = "卖家已发货，请注意物流签收"
    card.content_type, card.sender_name = 14, "交易消息"  # 提醒也是"发来一条新消息"
    store.save_messages(UNB, [shipped, card, person])
    assert store.forget_unsure_names() == 2
    assert store.forget_unsure_names() == 0
    assert [m.sender_name for m in store.messages(UNB, "777")[0]] == ["", "", "买家小王"]


def test_store_hides_deleted_conversations_until_a_new_message(store):
    hidden = Conversation.parse(conversation("778", UNB, "201", unread=1), UNB)
    hidden.visible = False  # 在 App 里删掉了，网页版不显示，未读也不算
    store.save_conversations(UNB, [conv("777", "200"), hidden])
    assert [c.cid for c in store.conversations(UNB)] == ["777"]
    assert store.conversation(UNB, "778") is not None
    store.add_live(UNB, live(5, cid="778", sender="201"))  # 来了新消息就又出现了
    assert {c.cid for c in store.conversations(UNB)} == {"777", "778"}


def test_store_returns_images_of_image_messages(store):
    store.save_conversations(UNB, [conv("777", "200")])
    image = ChatMessage.from_history(chat(1))
    image.content_type = 2
    image.raw["message"]["content"]["custom"]["data"] = base64.b64encode(
        json.dumps(
            {"contentType": 2, "image": {"pics": [{"url": "https://img/x.jpg", "width": 3}]}}
        ).encode()
    ).decode()
    pushed = ChatMessage.parse(decode_entry(entry(chat_message(message_id="p1"))["data"]))
    store.save_messages(UNB, [image, pushed])
    records = {m.message_id: m for m in store.messages(UNB, "777")[0]}
    assert records["m1"].images == [ChatImage("https://img/x.jpg", 3, None)]
    assert records["p1"].images == []
    assert store.message(UNB, "m1").images == records["m1"].images


def test_store_name_follows_the_list_until_looked_up(store):
    store.save_conversations(UNB, [conv("777", "200")])
    assert store.conversation(UNB, "777").peer_name == "买家小王"
    # 没查过的跟着会话列表走：列表里看不出来了，就不再用列表上次给的
    later = Conversation.parse(conversation("777", UNB, "200", last_from="me"), UNB)
    store.save_conversations(UNB, [later])
    assert store.conversation(UNB, "777").peer_name == ""
    # 查过的留着，列表里有新的才换
    store.set_peer_name(UNB, "777", "小林同学")
    store.save_conversations(UNB, [later])
    assert store.conversation(UNB, "777").peer_name == "小林同学"
    store.save_conversations(UNB, [conv("777", "200")])
    assert store.conversation(UNB, "777").peer_name == "买家小王"


# ---------- 拉消息 ----------


async def open_conn(server: FakeIm) -> ImConnection:
    return await ImConnection.open(
        cookies={"unb": UNB}, token="tok", device_id="DID", connect=server.connect, **FAST
    )


def test_fetch_newest_joins_local_and_older_fills_gaps(accounts, monkeypatch, store):
    monkeypatch.setattr(messages, "MESSAGE_PAGE", 5)
    monkeypatch.setattr(messages, "MAX_NEWEST_PAGES", 2)
    server = FakeIm()
    server.chats["777"] = [chat(n) for n in range(12)]
    hub = make_hub(store, accounts, server)
    store = hub.store
    store.save_conversations(UNB, [conv("777", "200")])

    def ids():
        return [r.message_id for r in store.messages(UNB, "777", limit=100)[0]]

    async def go():
        conn = await open_conn(server)
        # 头一次（本地没有）只拉一页，往前翻从第二页接着
        assert await hub.fetch_newest(UNB, "777", conn) == 5
        assert store.older(UNB, "777") == (T + 6000, True)
        assert await hub.fetch_older(UNB, "777", conn) == 5
        assert store.older(UNB, "777") == (T + 1000, True)
        assert await hub.fetch_older(UNB, "777", conn) == 2
        assert store.older(UNB, "777") == (None, False)
        assert await hub.fetch_older(UNB, "777", conn) == 0
        assert len(ids()) == 12

        # 来了两条新的：第一页就接上了
        server.chats["777"] += [chat(12), chat(13)]
        assert await hub.fetch_newest(UNB, "777", conn) == 2
        assert store.older(UNB, "777") == (None, False)

        # 断线期间来了 12 条：两页没接上，中间缺了第 14、15 条，往前翻时补上
        server.chats["777"] += [chat(n) for n in range(14, 26)]
        assert await hub.fetch_newest(UNB, "777", conn) == 10
        assert "m15" not in ids()
        assert store.older(UNB, "777") == (T + 15000, True)
        assert await hub.fetch_older(UNB, "777", conn) == 2
        await conn.close()

    run(go())
    assert ids() == [f"m{n}" for n in range(26)]
    assert not store.conversation(UNB, "777").stale


def card(n: int, order_id: str, role: str = "seller", *, cid: str = "777") -> dict:
    """第 n 条历史消息是一张交易卡片，时间 T + n 秒。"""
    return trade_card(
        order_id=order_id, role=role, cid=cid, message_id=f"m{n}", sent_ms=T + n * 1000
    )


def test_hub_hands_orders_in_new_messages_to_on_order(accounts, monkeypatch, store):
    monkeypatch.setattr(messages, "MESSAGE_PAGE", 3)
    server = FakeIm()
    server.chats["777"] = [card(0, "5001000000001"), chat(1), chat(2), chat(3), chat(4)]
    server.chats["777"].append(card(5, "5001000000002", "Buyer"))
    seen = []
    hub = MessageHub(
        store,
        accounts,
        defaultdict(asyncio.Lock),
        connect=server.connect,
        on_order=lambda *args: seen.append(args),
        **IM_OPTIONS,
    )
    store.save_conversations(UNB, [conv("777", "200")])

    async def go():
        conn = await open_conn(server)
        await hub.fetch_newest(UNB, "777", conn)
        assert seen == [(UNB, "5001000000002", "buyer")]
        await hub.fetch_older(UNB, "777", conn)
        assert seen[1:] == [(UNB, "5001000000001", "seller")]
        await hub.fetch_newest(UNB, "777", conn)  # 已经存过的不再交
        await conn.close()
        await hub._on_event(UNB, ChatMessage.from_history(card(6, "5001000000001")))

    run(go())
    assert seen[2:] == [(UNB, "5001000000001", "seller")]


def test_store_order_refs_from_messages_and_conversations(store):
    with_order = conversation("778", UNB, "201", modified_ms=T)
    with_order["singleChatUserConversation"]["singleChatConversation"]["extension"]["orderId"] = (
        "5001000000009"
    )
    store.save_conversations(UNB, [conv("777", "200"), Conversation.parse(with_order, UNB)])
    tip = history_message(text="[你已发货]", message_id="t1", sent_ms=T + 9000)
    tip["message"]["content"]["custom"]["type"] = 14
    store.save_messages(
        UNB,
        [
            live(1),
            ChatMessage.from_history(card(2, "5001000000001")),
            ChatMessage.from_history(card(3, "5001000000002", "buyer")),
            ChatMessage.parse(
                decode_entry(
                    entry(
                        trade_card(
                            order_id="5001000000003", message_id="p4", sent_ms=T + 4000, push=True
                        ),
                        packer="msgpack",
                    )["data"]
                )
            ),
            ChatMessage.from_history(tip),
        ],
    )
    assert store.order_refs(UNB) == [
        ("5001000000003", "seller"),
        ("5001000000002", "buyer"),
        ("5001000000001", "seller"),
        ("5001000000009", ""),
    ]


def test_hub_publish_reaches_subscribers(accounts, store):
    hub = make_hub(store, accounts, FakeIm())

    async def go():
        with hub.subscribe(UNB) as queue:
            hub.publish(UNB, {"type": "orders"})
            return queue.get_nowait()

    assert run(go()) == {"type": "orders"}


def test_fetch_needs_im_online(accounts, store):
    hub = make_hub(store, accounts, FakeIm())
    with pytest.raises(ImOfflineError):
        run(hub.fetch(UNB, "777"))


# ---------- 长期在线 ----------


def test_hub_backfills_on_connect_and_saves_live_messages(accounts, platform, store):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", unread=1, modified_ms=T + 2000),
        conversation("778", UNB, "201", modified_ms=T),
    ]
    server.chats = {
        "777": [chat(1), chat(2, sender=UNB)],
        "778": [chat(0, cid="778", sender="201")],
    }
    hub = make_hub(store, accounts, server)
    store = hub.store
    events = []

    def synced():
        convs = store.conversations(UNB)
        return len(convs) == 2 and not any(c.stale for c in convs)

    async def go():
        with hub.subscribe(UNB) as queue:
            await hub.start()
            await wait_until(lambda: synced() and hub.status(UNB)["state"] == "online")
            server.last.push_entries(
                [entry(chat_message(message_id="m9", sent_ms=T + 9000), packer="msgpack")]
            )
            await wait_until(lambda: store.conversation(UNB, "777").last_text == "还在吗")
            while not queue.empty():
                events.append(queue.get_nowait())
        await hub.aclose()

    run(go())
    assert platform.im_tokens == 1
    assert (accounts.root / f"{UNB}.im.json").exists()
    assert [m.text for m in store.messages(UNB, "777")[0]] == ["第1条", "第2条", "还在吗"]
    c = store.conversation(UNB, "777")
    assert c.unread == 2 and not c.stale
    types = [e["type"] for e in events]
    assert "conversations" in types and "messages" in types and "status" in types
    assert {"type": "message", "cid": "777", "message_id": "m9"} in events


def test_hub_looks_up_missing_names(accounts, platform, monkeypatch, store):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", modified_ms=T + 3000),  # 最后一条是对方的文本：有昵称
        conversation("778", UNB, "201", modified_ms=T + 2000, last_from="me"),  # 要查
        conversation("779", UNB, "202", modified_ms=T + 1000, last_from="me"),  # 消息里有昵称
        conversation("780", UNB, "203", modified_ms=T, last_from=None),  # 超出名额，点开时再查
    ]
    server.chats = {
        "777": [chat(1)],
        "778": [chat(2, cid="778", sender=UNB)],
        "779": [chat(3, cid="779", sender="202")],
        "780": [],
    }
    platform.nicks = {"778": "小林同学", "780": "晚风ovo"}
    monkeypatch.setattr(messages, "NAME_LOOKUPS", 1)
    hub = make_hub(store, accounts, server)
    store = hub.store

    async def go():
        await hub.start()
        await wait_until(
            lambda: (c := store.conversation(UNB, "778")) is not None and c.peer_name == "小林同学"
        )
        assert store.conversation(UNB, "780").peer_name == ""
        await hub.fetch(UNB, "780")  # 页面上点开
        await hub.aclose()

    run(go())
    names = {c.cid: c.peer_name for c in store.conversations(UNB)}
    assert names == {"777": "买家小王", "778": "小林同学", "779": "买家小王", "780": "晚风ovo"}
    assert [q["sessionId"] for q in platform.user_queries] == ["778", "780"]
    assert store.nameless(UNB, ["780"]) == []  # 查过了不再查


def test_hub_keeps_only_conversations_with_people(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", modified_ms=T + 5000),
        conversation("800", UNB, "0", modified_ms=T + 4000, biz_type="3"),  # 通知消息
        conversation("801", UNB, "0", modified_ms=T + 3000, biz_type="24"),  # 闲小蜜
        conversation("802", UNB, "202", modified_ms=T + 2000, biz_type="66"),  # 网页版不显示
        conversation("803", UNB, "203", modified_ms=T + 1000, biz_type="15"),
    ]
    hub = make_hub(store, accounts, server)
    store = hub.store
    # 以前存进来的系统会话（类型 23，对方是 0）和它的消息，启动时删掉
    old = conv("700", "0", modified_ms=T)
    old.biz_type = "23"
    store.save_conversations(UNB, [old])
    store.save_messages(UNB, [live(1, cid="700", sender="1400")])
    # 推送先建出来、还不知道类型的会话留着
    unknown = live(2, cid="701", sender="204")
    unknown.item_id = unknown.session_type = ""
    store.add_live(UNB, unknown)

    async def go():
        await hub.start()
        await wait_until(
            lambda: (
                hub.status(UNB)["state"] == "online" and not store.conversation(UNB, "777").stale
            )
        )
        promo = chat_message(cid="801", sender="1400", message_id="p1", session_type="24")
        server.last.push_entries([entry(promo, packer="msgpack")])
        server.last.push_entries([entry(chat_message(message_id="m9"), packer="msgpack")])
        await wait_until(lambda: store.conversation(UNB, "777").last_text == "还在吗")
        await hub.aclose()

    run(go())
    assert {c.cid for c in store.conversations(UNB)} == {"777", "803", "701"}
    assert store.messages(UNB, "700") == ([], False)
    assert store.messages(UNB, "801") == ([], False)


def test_hub_stops_when_login_expired_and_resumes_after_new_login(accounts, platform, store):
    platform.session_expired = True
    platform.renew_ok = False
    server = FakeIm()
    server.conversations_more = False
    hub = make_hub(store, accounts, server)

    async def go():
        await hub.start()
        await wait_until(lambda: hub.status(UNB)["needs"] == "login")
        status = hub.status(UNB)
        await asyncio.sleep(0.1)  # 几轮检查都不会再连
        assert not server.sockets

        platform.session_expired = False
        Credential(cookies={"unb": UNB, "cookie2": "new", "_m_h5_tk": "tok_2"}).save(
            accounts.path(UNB)
        )
        await wait_until(lambda: hub.status(UNB)["state"] == "online")
        await hub.aclose()
        return status

    status = run(go())
    assert status["state"] == "stopped" and status["needs"] == "login"
    assert len(server.sockets) == 1


def test_hub_waits_for_manual_reconnect_after_session_removed(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    hub = make_hub(store, accounts, server)

    async def go():
        await hub.start()
        await wait_until(lambda: hub.status(UNB)["state"] == "online")
        server.last.push("/s/session/remove")
        await wait_until(lambda: hub.status(UNB)["state"] == "stopped")
        await asyncio.sleep(0.1)
        status = hub.status(UNB)
        assert len(server.sockets) == 1

        hub.restart(UNB)
        assert hub.status(UNB)["state"] == "connecting"
        await wait_until(lambda: hub.status(UNB)["state"] == "online")
        hub.restart(UNB)  # 在线时重新连接：先关掉旧的
        await wait_until(lambda: len(server.sockets) == 3 and hub.status(UNB)["state"] == "online")
        await hub.aclose()
        return status

    status = run(go())
    assert status["needs"] == "reconnect"
    assert all(s.closed for s in server.sockets)


def test_im_lock_one_holder_at_a_time(tmp_path):
    path = tmp_path / "42.im.lock"
    first, second = ImLock(path), ImLock(path)
    assert first.acquire() and first.acquire()  # 自己再抢一次也行
    assert not second.acquire()
    assert second.holder() == str(os.getpid())
    first.release()
    assert second.acquire() and second.held
    second.release()
    assert path.exists()  # 不删文件


def test_removed_account_is_disconnected_and_stays_off(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    hub = make_hub(store, accounts, server)

    async def go():
        await hub.start()
        await wait_until(lambda: hub.status(UNB)["state"] == "online")
        accounts.remove(UNB)
        await hub.stop(UNB)
        await asyncio.sleep(0.1)  # 看了几轮账号也不再连
        status = hub.status(UNB)
        await hub.aclose()
        return status

    assert run(go())["state"] == "stopped"
    assert ImLock(accounts.root / f"{UNB}.im.lock").acquire()  # 锁放开了
    assert len(server.sockets) == 1


def test_second_hub_does_not_connect_while_another_holds_the_account(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    first, second = make_hub(store, accounts, server), make_hub(store, accounts, server)

    async def go():
        await first.start()
        await wait_until(lambda: first.status(UNB)["state"] == "online")
        await second.start()
        await wait_until(lambda: second.status(UNB)["needs"] == "elsewhere")
        await asyncio.sleep(0.1)  # 抢了几轮都没抢到
        status = second.status(UNB)
        assert status["state"] == "stopped" and str(os.getpid()) in status["message"]
        assert len(server.sockets) == 1 and first.status(UNB)["state"] == "online"
        with pytest.raises(ImOfflineError, match="另一个 xym serve"):
            await second.fetch(UNB, "777")

        await first.aclose()  # 那边退出了，这边自动接上
        await wait_until(lambda: second.status(UNB)["state"] == "online")
        assert len(server.sockets) == 2
        await second.aclose()

    run(go())


# ---------- 标已读 ----------


def test_hub_marks_read_with_the_newest_message(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", unread=2, modified_ms=T + 3000),
        conversation("778", UNB, "201", unread=1, modified_ms=T + 2000),
        conversation("779", UNB, "202", modified_ms=T + 1000),
    ]
    server.chats = {"777": [chat(1), chat(2)], "778": [], "779": [chat(3, cid="779")]}
    hub = make_hub(store, accounts, server)
    store = hub.store
    events = []

    async def go():
        await start_synced(hub)
        with hub.subscribe(UNB) as queue:
            assert await hub.mark_read(UNB, "777") == 2
            assert await hub.mark_read(UNB, "777") == 0  # 已经没有未读，不再发请求
            assert await hub.mark_read(UNB, "779") == 0
            assert await hub.mark_read(UNB, "778") == 0  # 一条消息也没有，没法清
            while not queue.empty():
                events.append(queue.get_nowait())
        await hub.aclose()
        with pytest.raises(ImOfflineError):
            await hub.mark_read(UNB, "778")

    run(go())
    assert server.cleared == [{"messageId": "m2", "cid": "777@goofish"}]
    assert store.conversation(UNB, "777").unread == 0
    assert store.conversation(UNB, "778").unread == 1
    assert {"type": "conversations"} in events


def test_hub_mark_read_fetches_newest_first_when_behind(accounts, store):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [conversation("777", UNB, "200", unread=1, modified_ms=T + 9000)]
    server.chats = {"777": [chat(1)]}
    hub = make_hub(store, accounts, server)

    async def go():
        await start_synced(hub)
        hub.store.save_conversations(UNB, [conv("777", "200", unread=1, modified_ms=T + 20000)])
        server.chats["777"].append(chat(9))  # 平台上又来了一条，本地还没拉
        assert hub.store.conversation(UNB, "777").stale
        await hub.mark_read(UNB, "777")
        await hub.aclose()

    run(go())
    assert server.cleared == [{"messageId": "m9", "cid": "777@goofish"}]


# ---------- 发消息 ----------


def send_server() -> FakeIm:
    """一个商品会话 777（对方 200，有一条未读）和一个不是商品会话的聊天 801（类型 15）。"""
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", unread=1, modified_ms=T + 2000),
        conversation("801", UNB, "201", modified_ms=T, biz_type="15"),
    ]
    server.chats = {"777": [chat(1)], "801": []}
    return server


async def start_synced(hub: MessageHub) -> None:
    await hub.start()
    await wait_until(
        lambda: (
            hub.status(UNB)["state"] == "online"
            and not any(c.stale for c in hub.store.conversations(UNB))
        )
    )


@pytest.mark.parametrize("echo", ["after", "before", None])
def test_hub_sends_reply_and_stores_it_once(accounts, echo, store):
    server = send_server()
    server.send_echo = echo
    hub = make_hub(store, accounts, server)
    events = []

    async def go():
        await start_synced(hub)
        with hub.subscribe(UNB) as queue:
            acks = len(server.last.acks)
            record = await hub.send(UNB, "777", "  在的，今天能发货\n")
            if echo:  # 等推回来的那条也处理完
                await wait_until(lambda: len(server.last.acks) > acks)
                await asyncio.sleep(0.02)
            while not queue.empty():
                events.append(queue.get_nowait())
        await hub.aclose()
        return record

    record = run(go())
    assert (record.text, record.mine, record.content_type) == ("在的，今天能发货", True, 1)
    messages = hub.store.messages(UNB, "777")[0]
    assert [m.text for m in messages] == ["第1条", "在的，今天能发货"]
    c = hub.store.conversation(UNB, "777")
    assert c.last_text == "在的，今天能发货" and c.unread == 0 and not c.stale
    assert {"type": "message", "cid": "777", "message_id": record.message_id} in events


def test_hub_send_checks_before_sending(accounts, store):
    server = send_server()
    hub = make_hub(store, accounts, server)

    async def go():
        await start_synced(hub)
        with pytest.raises(ValueError, match="空的"):
            await hub.send(UNB, "777", " \n ")
        with pytest.raises(ValueError, match="最多 500 个字"):
            await hub.send(UNB, "777", "好" * 501)
        with pytest.raises(ValueError, match="只能回复商品会话"):
            await hub.send(UNB, "801", "在的")
        with pytest.raises(ValueError, match="本地没有会话"):
            await hub.send(UNB, "999", "在的")
        await hub.aclose()
        with pytest.raises(ImOfflineError):
            await hub.send(UNB, "777", "在的")

    run(go())
    assert server.sends == 0


def test_hub_send_rate_limit_and_uncertain_result(accounts, store):
    accounts.guard = Guard(limits={SEND: 1}, max_wait=0)
    server = send_server()
    hub = make_hub(store, accounts, server)

    async def go():
        await start_synced(hub)
        server.send_reply = "silent"
        with pytest.raises(ImSendUncertainError):
            await hub.send(UNB, "777", "在的")
        with pytest.raises(RateLimitedError, match="发消息太频繁"):
            await hub.send(UNB, "777", "在的")
        await hub.aclose()

    run(go())
    assert server.sends == 1  # 没结果的不自动重发，被限流的没发出去
    assert [m.text for m in hub.store.messages(UNB, "777")[0]] == ["第1条"]


def test_sse_sends_status_then_events_and_pings(accounts, store):
    hub = make_hub(store, accounts, FakeIm())

    async def go():
        stream = sse_events(hub, UNB, ping=0.05)
        first = await anext(stream)
        hub._publish(UNB, {"type": "message", "cid": "777", "message_id": "m1"})
        second = await anext(stream)
        third = await anext(stream)
        await stream.aclose()
        return first, second, third

    first, second, third = run(go())
    assert first.startswith("event: status\ndata: ") and '"state": "off"' in first
    assert second == (
        'event: message\ndata: {"type": "message", "cid": "777", "message_id": "m1"}\n\n'
    )
    assert third == ": ping\n\n"


def test_sse_ends_when_access_is_gone(accounts, store):
    """流开着的时候账号转给了别人、用户被停用：下一次 ping 时结束，页面重连时就进不来了。"""
    hub = make_hub(store, accounts, FakeIm())
    allowed = [True]

    async def go():
        stream = sse_events(hub, UNB, ping=0.01, allowed=lambda: allowed[0])
        await anext(stream)
        assert await anext(stream) == ": ping\n\n"
        allowed[0] = False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    run(go())
    assert not hub._subscribers[UNB]
    assert not hub._subscribers[UNB]


def test_sse_ends_when_server_is_closing(accounts, store):
    hub = make_hub(store, accounts, FakeIm())

    async def go():
        stream = sse_events(hub, UNB, ping=5)
        await anext(stream)
        hub.close_streams()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        # 关的时候才连上来的，发完状态就结束
        late = sse_events(hub, UNB, ping=5)
        await anext(late)
        with pytest.raises(StopAsyncIteration):
            await anext(late)

    run(go())
    assert not hub._subscribers[UNB]


# ---------- 接口 ----------


def test_api_reads_local_messages_when_im_off(accounts, platform, store, database_url):
    store.save_conversations(UNB, [conv("777", "200", unread=1)])
    store.save_messages(UNB, [live(n) for n in range(3)])
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        web_dist=None,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        check_local_api(client)


def check_local_api(client):
    body = client.get(f"/api/accounts/{UNB}/conversations").json()
    assert body["im"]["state"] == "off"
    assert [c["cid"] for c in body["conversations"]] == ["777"]
    assert body["conversations"][0]["last_text"] == "第2条"

    body = client.get(f"/api/accounts/{UNB}/conversations/777/messages?limit=2").json()
    assert [m["text"] for m in body["messages"]] == ["第1条", "第2条"]
    assert body["local_more"] and "raw" not in body["messages"][0]

    resp = client.post(f"/api/accounts/{UNB}/conversations/777/fetch", json={})
    assert resp.status_code == 503 and resp.json()["code"] == "im_offline"
    resp = client.post(f"/api/accounts/{UNB}/im/reconnect")
    assert resp.status_code == 503
    resp = client.get(f"/api/accounts/{UNB}/conversations/999/messages")
    assert resp.status_code == 400


def test_api_with_im_online(accounts, platform, database_url):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [conversation("777", UNB, "200", modified_ms=T + 3000)]
    server.chats = {"777": [chat(n) for n in range(4)]}
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        web_dist=None,
        messages=True,
        im_connect=server.connect,
        im_options=IM_OPTIONS,
    )
    url = f"/api/accounts/{UNB}"
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        deadline = time.monotonic() + 3
        while True:
            body = client.get(f"{url}/conversations").json()
            convs = body["conversations"]
            if body["im"]["state"] == "online" and convs and not convs[0]["stale"]:
                break
            assert time.monotonic() < deadline, body
            time.sleep(0.01)
        body = client.get(f"{url}/conversations/777/messages").json()
        assert [m["text"] for m in body["messages"]] == ["第0条", "第1条", "第2条", "第3条"]

        resp = client.post(f"{url}/conversations/777/fetch", json={"older": True})
        assert resp.json() == {"added": 0, "older_more": False}

        resp = client.post(f"{url}/conversations/777/messages", json={"text": "在的"})
        assert resp.status_code == 200
        sent = resp.json()["message"]
        assert (sent["text"], sent["mine"], sent["cid"]) == ("在的", True, "777")
        body = client.get(f"{url}/conversations/777/messages").json()
        assert body["messages"][-1]["message_id"] == sent["message_id"]
        resp = client.post(f"{url}/conversations/777/messages", json={"text": " "})
        assert (resp.status_code, resp.json()["code"]) == (400, "invalid")
        server.send_reply = "silent"
        resp = client.post(f"{url}/conversations/777/messages", json={"text": "好的"})
        assert (resp.status_code, resp.json()["code"]) == (504, "send_uncertain")

        assert client.post(f"{url}/conversations/777/read").json() == {"cleared": 0}
        assert client.post(f"{url}/conversations/999/read").status_code == 400

        assert client.post(f"{url}/im/reconnect").status_code == 200
    assert all(s.closed for s in server.sockets)


def test_api_records_orders_from_trade_messages(accounts, platform, database_url):
    """补拉到的、实时推来的交易卡片里的订单，自动记进本地库，状态跟着卡片刷新。"""
    sold, bought = "5003000000001", "5003000000002"
    for order_id, extra in ((sold, {}), (bought, {"bought": True})):
        platform.orders[order_id] = {
            "item_id": "1001",
            "status": "等待买家付款",
            "price": 35800,
            "post": 0,
            "created": 1761900000000,
            **extra,
        }
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [
        conversation("777", UNB, "200", modified_ms=T + 3000),
        conversation("778", UNB, "201", modified_ms=T + 2000),
    ]
    server.chats = {
        "777": [chat(1), card(2, sold)],
        "778": [card(1, bought, "buyer", cid="778")],
    }
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
        messages=True,
        im_connect=server.connect,
        im_options=IM_OPTIONS,
    )
    url = f"/api/accounts/{UNB}"
    add_user(database_url, accounts=[UNB])

    def orders(client) -> dict[str, str]:
        return {o["order_id"]: o["status"] for o in client.get(f"{url}/orders").json()["orders"]}

    def wait_for(client, expected: dict[str, str]) -> None:
        deadline = time.monotonic() + 3
        while orders(client) != expected:
            assert time.monotonic() < deadline, orders(client)
            time.sleep(0.01)

    with TestClient(app) as client:
        sign_in(client)
        wait_for(client, {sold: "unpaid"})  # 补拉到的卡片
        platform.orders[sold]["status"] = "等待卖家发货"
        server.last.push_entries(
            [
                entry(
                    trade_card(order_id=sold, message_id="c9", sent_ms=T + 9000, push=True),
                    packer="msgpack",
                )
            ]
        )
        wait_for(client, {sold: "to_ship"})
    details = [d["tid"] for api, d in platform.calls if api.endswith("order.detail")]
    assert bought not in details  # 卡片上写着是买家的不拉


def test_api_trade_message_triggers_item_sync(accounts, platform, database_url):
    """卖出的订单从交易消息里新记下，过一会儿自动同步一次商品（卖掉的商品状态跟着变）。"""
    sold = "5003000000001"
    platform.orders[sold] = {
        "item_id": "1001",
        "status": "等待卖家发货",
        "price": 35800,
        "post": 0,
        "created": 1761900000000,
    }
    now = time.time()
    conn = db.connect(database_url)
    # 定时同步都刚跑过，这次的商品同步只能是交易消息触发的
    conn.executemany(
        "INSERT INTO sync_jobs (account_id, job, last_run_at, next_run_at) VALUES (%s, %s, %s, %s)",
        [(UNB, job, now, now + 3600) for job in ("items", "sold", "bought")],
    )
    conn.close()
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [conversation("777", UNB, "200", modified_ms=T + 3000)]
    server.chats = {"777": [chat(1), card(2, sold)]}
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
        messages=True,
        im_connect=server.connect,
        im_options=IM_OPTIONS,
        auto_sync=True,
        auto_sync_options={"tick": 3600, "trigger_delay": 0},
    )
    url = f"/api/accounts/{UNB}"
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        deadline = time.monotonic() + 3
        while True:
            jobs = {j["job"]: j for j in client.get(f"{url}/autosync").json()["jobs"]}
            if jobs["items"]["last_trigger"] == "message":
                break
            assert time.monotonic() < deadline, jobs
            time.sleep(0.01)
        items = client.get(f"{url}/items").json()["items"]
    assert sorted(i["item_id"] for i in items) == ["1001", "1002"]
    assert jobs["sold"]["last_trigger"] is None  # 订单的定时同步没跑
