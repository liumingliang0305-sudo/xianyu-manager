import asyncio
import time
from collections import defaultdict

import httpx
import pytest
from fake_im import FakeIm, chat_message, conversation, entry, history_message
from fakes import UNB
from fastapi.testclient import TestClient

from app import db, messages
from app.api import create_app, sse_events
from app.messages import ImOfflineError, MessageHub, MessageStore
from xianyu_core.credential import Credential
from xianyu_core.im import ChatMessage, Conversation, ImConnection

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


def make_store() -> MessageStore:
    return MessageStore(db.connect(":memory:"))


def make_hub(accounts, server: FakeIm) -> MessageHub:
    return MessageHub(
        make_store(), accounts, defaultdict(asyncio.Lock), connect=server.connect, **IM_OPTIONS
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


def test_store_conversations_and_messages():
    store = make_store()
    store.save_conversations(UNB, [conv("777", "200", unread=2, modified_ms=T + 5000)])
    store.save_conversations(UNB, [conv("778", "201", modified_ms=T + 9000)])
    assert [c.cid for c in store.conversations(UNB)] == ["778", "777"]

    c = store.conversation(UNB, "777")
    assert (c.peer_id, c.peer_name, c.item_title, c.unread) == ("200", "买家小王", "洗衣机", 2)
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


def test_store_live_message_updates_conversation():
    store = make_store()
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


def test_store_peer_name_falls_back_to_text_messages():
    store = make_store()
    nameless = conversation("777", UNB, "200")
    del nameless["singleChatUserConversation"]["singleChatConversation"]["extension"][
        "squadName_200"
    ]
    store.save_conversations(UNB, [Conversation.parse(nameless, UNB)])
    assert store.conversation(UNB, "777").peer_name == ""

    card = live(1)
    card.content_type, card.sender_name = 14, "交易消息"  # 交易卡片的提醒标题不是昵称
    store.save_messages(UNB, [card])
    assert store.conversation(UNB, "777").peer_name == ""
    store.save_messages(UNB, [live(2)])
    assert store.conversation(UNB, "777").peer_name == "买家小王"


# ---------- 拉消息 ----------


async def open_conn(server: FakeIm) -> ImConnection:
    return await ImConnection.open(
        cookies={"unb": UNB}, token="tok", device_id="DID", connect=server.connect, **FAST
    )


def test_fetch_newest_joins_local_and_older_fills_gaps(accounts, monkeypatch):
    monkeypatch.setattr(messages, "MESSAGE_PAGE", 5)
    monkeypatch.setattr(messages, "MAX_NEWEST_PAGES", 2)
    server = FakeIm()
    server.chats["777"] = [chat(n) for n in range(12)]
    hub = make_hub(accounts, server)
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


def test_fetch_needs_im_online(accounts):
    hub = make_hub(accounts, FakeIm())
    with pytest.raises(ImOfflineError):
        run(hub.fetch(UNB, "777"))


# ---------- 长期在线 ----------


def test_hub_backfills_on_connect_and_saves_live_messages(accounts, platform):
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
    hub = make_hub(accounts, server)
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


def test_hub_stops_when_login_expired_and_resumes_after_new_login(accounts, platform):
    platform.session_expired = True
    platform.renew_ok = False
    server = FakeIm()
    server.conversations_more = False
    hub = make_hub(accounts, server)

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


def test_hub_waits_for_manual_reconnect_after_session_removed(accounts):
    server = FakeIm()
    server.conversations_more = False
    hub = make_hub(accounts, server)

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


def test_sse_sends_status_then_events_and_pings(accounts):
    hub = make_hub(accounts, FakeIm())

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
    assert not hub._subscribers[UNB]


def test_sse_ends_when_server_is_closing(accounts):
    hub = make_hub(accounts, FakeIm())

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


def test_api_reads_local_messages_when_im_off(tmp_path, accounts, platform):
    db_path = tmp_path / "xianyu.db"
    store = MessageStore(db.connect(db_path))
    store.save_conversations(UNB, [conv("777", "200", unread=1)])
    store.save_messages(UNB, [live(n) for n in range(3)])
    app = create_app(
        db_path=db_path,
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        web_dist=None,
    )
    with TestClient(app) as client:
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


def test_api_with_im_online(accounts, platform):
    server = FakeIm()
    server.conversations_more = False
    server.conversations = [conversation("777", UNB, "200", modified_ms=T + 3000)]
    server.chats = {"777": [chat(n) for n in range(4)]}
    app = create_app(
        db_path=":memory:",
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        web_dist=None,
        messages=True,
        im_connect=server.connect,
        im_options=IM_OPTIONS,
    )
    url = f"/api/accounts/{UNB}"
    with TestClient(app) as client:
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
        assert client.post(f"{url}/im/reconnect").status_code == 200
    assert all(s.closed for s in server.sockets)
