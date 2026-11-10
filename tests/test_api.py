import httpx
import pytest
from fake_im import trade_card
from fakes import UNB, add_user, keep_login_cookies, png, sign_in
from fastapi.testclient import TestClient

from app import db
from app.api import create_app
from app.messages import MessageStore
from xianyu_core.credential import Credential
from xianyu_core.guard import Guard
from xianyu_core.im import ChatMessage


@pytest.fixture
def client(tmp_path, accounts, platform, database_url):
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        yield client


def items_url(path=""):
    return f"/api/accounts/{UNB}/items{path}"


def test_accounts_and_sync_then_list(client):
    assert [a["id"] for a in client.get("/api/accounts").json()["accounts"]] == [UNB]
    assert client.get(items_url()).json() == {"items": [], "last_synced_at": None}

    result = client.post(items_url("/sync")).json()
    assert sorted(result["added"]) == ["1001", "1002"]

    body = client.get(items_url()).json()
    assert body["last_synced_at"] is not None
    by_id = {it["item_id"]: it for it in body["items"]}
    assert by_id["1001"]["price_cent"] == 35800
    assert by_id["1001"]["browse_count"] is None  # 浏览、收藏同步时不拉
    assert [it["item_id"] for it in client.get(items_url("?status=1")).json()["items"]] == ["1002"]


def test_detail_comes_from_the_seller_form(client, platform):
    """点开详情不能调买家看的 pc.detail（会被要滑块、账号熔断），老页面带着 stats 同步也一样。"""
    client.post(items_url("/sync"), json={"stats": True})
    platform.items["1001"]["desc"] = "洗衣机 九成新\n自提"
    detail = client.get(items_url("/1001/detail")).json()
    assert detail == {
        "item_id": "1001",
        "desc": "洗衣机 九成新\n自提",
        "price_cent": 35800,
        "orig_price_cent": 0,
        "quantity": 1,
        "image_urls": ["http://img.example/1001.jpg"],
        "multi_sku": False,
    }
    assert "mtop.taobao.idle.pc.detail" not in [api for api, _ in platform.calls]
    assert client.get("/api/accounts").json()["accounts"][0]["block"] is None


def test_item_stats_from_the_extension(client, platform):
    client.post(items_url("/sync"))
    sent = len(platform.calls)
    item = {"itemId": 1001, "wantCnt": 5, "browseCnt": 100, "collectCnt": 2, "desc": "不存"}
    saved = client.post(items_url("/1001/stats"), json={"item": item}).json()
    assert (saved["want_count"], saved["browse_count"], saved["collect_count"]) == (5, 100, 2)
    assert (saved["item_id"], saved["price_cent"]) == ("1001", 35800)
    [listed] = [it for it in client.get(items_url()).json()["items"] if it["item_id"] == "1001"]
    assert listed == saved
    assert len(platform.calls) == sent  # 不请求闲鱼

    resp = client.post(items_url("/1002/stats"), json={"item": item})  # 不是这个商品的
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid")
    resp = client.post(items_url("/7777/stats"), json={"item": {**item, "itemId": 7777}})
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid")
    assert client.post(items_url("/1001/stats"), json={}).status_code == 422
    assert client.post("/api/accounts/999/items/1001/stats", json={"item": item}).status_code == 404


def test_change_price_with_guard_and_force(client, platform):
    resp = client.post(items_url("/1001/price"), json={"price": "299"})
    assert resp.json() == {"old_price_cent": 35800, "price_cent": 29900}

    resp = client.post(items_url("/1001/price"), json={"price": "2.99"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "price_guard"
    assert platform.items["1001"]["price"] == 29900

    resp = client.post(items_url("/1001/price"), json={"price": "2.99", "force": True})
    assert resp.status_code == 200
    assert platform.items["1001"]["price"] == 299

    ops = client.get(f"/api/accounts/{UNB}/item-ops?item_id=1001").json()["ops"]
    assert [op["detail"]["to"] for op in ops] == [299, 29900]


@pytest.mark.parametrize("price", ["abc", "1.234", "0"])
def test_invalid_price_is_400(client, price):
    resp = client.post(items_url("/1001/price"), json={"price": price})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"


@pytest.mark.parametrize(
    ("ret", "status", "code"),
    [
        ("FAIL_SYS_SESSION_EXPIRED::Session过期", 401, "auth_required"),
        ("RGV587_ERROR::SM::哎哟喂,被挤爆啦", 503, "risk_control"),
        ("FAIL_BIZ_ITEM_STATUS_ERROR::宝贝状态不允许编辑", 502, "platform_error"),
    ],
)
def test_platform_errors_map_to_codes(client, platform, ret, status, code):
    platform.fail["mtop.idle.pc.idleitem.editDetail"] = ret
    resp = client.post(items_url("/1001/price"), json={"price": "299"})
    assert resp.status_code == status
    assert resp.json()["code"] == code


def login_checks(platform):
    return sum(api == "mtop.taobao.idlemessage.pc.loginuser.get" for api, _ in platform.calls)


def test_accounts_carry_login_status_and_reuse_it(client, platform):
    login = client.get("/api/accounts").json()["accounts"][0]["login"]
    assert login["state"] == "valid"
    assert login["renewed"] is False
    assert login_checks(platform) == 1

    client.get("/api/accounts")
    assert login_checks(platform) == 1  # 刚确认过，不再请求平台

    # 别的接口碰到会话失效（续期也没成功），账号列表直接反映出来
    platform.session_expired = True
    assert client.post(items_url("/sync"), json={}).status_code == 401
    login = client.get("/api/accounts").json()["accounts"][0]["login"]
    assert login["state"] == "expired"
    assert "保持登录" in login["message"]
    assert login_checks(platform) == 1


def test_check_endpoint_rechecks_and_renews(client, accounts, platform):
    Credential(
        cookies={"unb": UNB, "_m_h5_tk": "tok_1", "cookie2": "old", **keep_login_cookies()}
    ).save(accounts.path(UNB))
    client.get("/api/accounts")
    platform.session_expired = True

    body = client.post(f"/api/accounts/{UNB}/check").json()

    assert body["id"] == UNB
    assert body["login"]["state"] == "valid"
    assert body["login"]["renewed"] is True
    assert body["login"]["keep_login_until"] is not None
    assert platform.renews == 1
    assert login_checks(platform) == 3  # 第一次确认、过期、续期后重试


def test_unknown_account_is_404(client):
    resp = client.get("/api/accounts/999/items")
    assert resp.status_code == 404
    assert resp.json()["code"] == "account_not_found"


def test_downshelf_delete(client, platform):
    client.post(items_url("/sync"), json={})
    assert client.post(items_url("/1001/downshelf")).status_code == 200
    assert client.delete(items_url("/1002")).status_code == 200
    assert client.get(items_url()).json()["items"] == []
    gone = client.get(items_url("?include_gone=true")).json()["items"]
    assert [(it["item_id"], it["on_platform"]) for it in gone] == [("1001", False)]


def test_serves_built_frontend_with_spa_fallback(tmp_path, accounts, database_url):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "main.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("secret")

    app = create_app(
        database_url=database_url, guard=Guard(), accounts_dir=accounts.root, web_dist=dist
    )
    with TestClient(app) as client:
        assert client.get("/").text == "<html>app</html>"
        assert client.get("/items/1001").text == "<html>app</html>"
        assert client.get("/assets/main.js").text == "console.log(1)"
        assert client.get("/favicon.svg").text == "<svg/>"
        assert client.get("/api/nope").status_code == 404
        assert "secret" not in client.get("/..%2Fsecret.txt").text


def orders_url(path=""):
    return f"/api/accounts/{UNB}/orders{path}"


def test_orders_sync_list_and_detail(client):
    assert client.get(orders_url()).json() == {"orders": [], "last_synced_at": None}
    result = client.post(orders_url("/sync")).json()
    assert sorted(result["added"]) == ["5001", "5002"]

    body = client.get(orders_url()).json()
    assert [o["order_id"] for o in body["orders"]] == ["5002", "5001"]
    assert "raw" not in body["orders"][0]
    assert [o["order_id"] for o in client.get(orders_url("?status=done")).json()["orders"]] == [
        "5001"
    ]

    detail = client.get(orders_url("/5002/detail")).json()
    assert detail["amount_cent"] == 36800
    assert detail["fields"][0] == {"title": "订单编号", "value": "5002"}
    assert "raw" not in detail


def test_order_price_flow(client, platform):
    fields = client.get(orders_url("/5002/price-fields")).json()["fields"]
    assert [(f["key"], f["price_cent"]) for f in fields] == [
        ("modifyFee", 35800),
        ("newTransportFee", 1000),
    ]

    resp = client.post(orders_url("/5002/price"), json={"fees": {"modifyFee": "3"}})
    assert resp.status_code == 409
    assert resp.json()["code"] == "price_guard"

    resp = client.post(orders_url("/5002/price"), json={"fees": {"modifyFee": "abc"}})
    assert resp.status_code == 400

    resp = client.post(
        orders_url("/5002/price"), json={"fees": {"modifyFee": "299", "newTransportFee": "0"}}
    )
    assert resp.json() == {"old_price_cent": 36800, "price_cent": 29900}
    assert platform.orders["5002"]["post"] == 0

    ops = client.get(f"/api/accounts/{UNB}/order-ops?order_id=5002").json()["ops"]
    assert [(op["detail"]["to"], op["ok"]) for op in ops] == [(29900, True)]


def test_order_price_not_allowed_is_400(client):
    resp = client.post(orders_url("/5001/price"), json={"fees": {"modifyFee": "1"}})
    assert resp.status_code == 400
    assert "待付款" in resp.json()["message"]


def test_add_and_remove_order(client, platform):
    platform.orders["5003000000001"] = {
        "item_id": "1001",
        "status": "等待卖家发货",
        "price": 35800,
        "post": 0,
        "created": 1761900000000,
    }
    resp = client.post(
        orders_url(), json={"order": "https://www.goofish.com/order-detail?orderId=5003000000001"}
    )
    body = resp.json()
    assert body["added"] is True
    assert (body["order"]["order_id"], body["order"]["status"]) == ("5003000000001", "to_ship")
    assert client.post(orders_url(), json={"order": "5003000000001"}).json()["added"] is False

    assert client.post(orders_url(), json={"order": "abc"}).status_code == 400
    assert client.delete(orders_url("/5003000000001")).json() == {"ok": True}
    assert client.delete(orders_url("/5003000000001")).status_code == 400
    assert client.get(orders_url()).json()["orders"] == []


def test_sync_without_list_permission(client, platform):
    platform.fail["mtop.taobao.idle.trade.merchant.sold.get"] = "PERMISSION_EXCEPTION::无权限访问"
    result = client.post(orders_url("/sync")).json()
    assert result["source"] == "detail"
    assert result["updated"] == []


def test_risk_control_trips_breaker_until_unblocked(client, platform):
    platform.fail["mtop.idle.pc.idleitem.editDetail"] = "RGV587_ERROR::SM::哎哟喂,被挤爆啦"
    assert client.post(items_url("/1001/price"), json={"price": "299"}).json()["code"] == (
        "risk_control"
    )

    sent = len(platform.calls)
    resp = client.get(items_url("/1001/detail"))
    assert resp.status_code == 503
    assert resp.json()["code"] == "circuit_open"
    assert len(platform.calls) == sent  # 熔断期间一个请求都没发

    [acc] = client.get("/api/accounts").json()["accounts"]
    assert "RGV587" in acc["block"]["reason"]
    assert acc["login"]["state"] == "risk"
    assert len(platform.calls) == sent  # 看账号列表也没去确认登录态

    # 解除后风控登录态一并忘掉，再看账号列表时重新确认一次
    del platform.fail["mtop.idle.pc.idleitem.editDetail"]
    assert client.delete(f"/api/accounts/{UNB}/block").json() == {"ok": True}
    [acc] = client.get("/api/accounts").json()["accounts"]
    assert acc["block"] is None
    assert acc["login"]["state"] == "valid"
    assert client.get(items_url("/1001/detail")).status_code == 200


def test_writes_are_rate_limited(client, platform):
    for price in ("300", "310", "320"):
        assert client.post(items_url("/1001/price"), json={"price": price}).status_code == 200
    resp = client.post(items_url("/1001/price"), json={"price": "330"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"
    assert platform.items["1001"]["price"] == 32000


def search_url():
    return f"/api/accounts/{UNB}/search"


def test_search_returns_cards_without_raw(client, platform):
    body = client.post(
        search_url(),
        json={"keyword": "洗衣", "min_price": "100", "filters": ["personal"], "sort": "new"},
    ).json()
    assert [it["item_id"] for it in body["items"]] == ["1001"]
    assert body["items"][0]["price_cent"] == 35800
    assert "raw" not in body["items"][0]
    assert (body["page"], body["has_next"], body["total"]) == (1, False, 1)

    api, data = platform.calls[-1]
    assert api == "mtop.taobao.idlemtopsearch.pc.search"
    assert data["propValueStr"] == {
        "searchFilter": "priceRange:100,undefined;quickFilter:filterPersonal;"
    }
    assert (data["sortField"], data["sortValue"]) == ("create", "desc")


@pytest.mark.parametrize(
    "body",
    [
        {"keyword": " "},
        {"keyword": "洗衣", "sort": "cheap"},
        {"keyword": "洗衣", "filters": ["nope"]},
        {"keyword": "洗衣", "min_price": "abc"},
        {"keyword": "洗衣", "min_price": "500", "max_price": "100"},
        {"keyword": "洗衣", "page": 51},
    ],
)
def test_search_bad_conditions_are_400(client, platform, body):
    sent = len(platform.calls)
    resp = client.post(search_url(), json=body)
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"
    assert len(platform.calls) == sent


def test_search_risk_control_trips_breaker(client, platform):
    platform.fail["mtop.taobao.idlemtopsearch.pc.search"] = "RGV587_ERROR::SM::哎哟喂,被挤爆啦"
    assert client.post(search_url(), json={"keyword": "洗衣"}).json()["code"] == "risk_control"
    assert client.get(items_url("/1001/detail")).json()["code"] == "circuit_open"


def test_scan_orders_from_stored_messages(client, database_url, platform):
    """以前存下的交易消息里的订单补进本地库：卖出的、买到的都记，查不到的报平台出错。"""
    order = {"item_id": "1001", "status": "等待卖家发货", "price": 35800, "post": 0}
    platform.orders["5001000000001"] = {**order, "created": 1761700000000}
    platform.orders["5001000000002"] = {**order, "created": 1761800000000, "bought": True}
    cards = [
        ("5001000000001", "seller"),
        ("5001000000002", "buyer"),
        ("5001000000004", "seller"),  # 平台查不到
    ]
    conn = db.connect(database_url)
    MessageStore(conn).save_messages(
        UNB,
        [
            ChatMessage.from_history(
                trade_card(order_id=o, role=role, message_id=f"m{i}", sent_ms=1793378127000 - i)
            )
            for i, (o, role) in enumerate(cards)
        ],
    )
    conn.close()

    body = client.post(orders_url("/scan")).json()
    assert body["found"] == 3
    assert body["added"] == ["5001000000001", "5001000000002"]
    assert body["failed"] == ["5001000000004"]
    assert body["updated"] == body["skipped"] == body["missing"] == []
    listed = {o["order_id"]: o["side"] for o in client.get(orders_url()).json()["orders"]}
    assert listed == {"5001000000001": "sold", "5001000000002": "bought"}
    assert [o["order_id"] for o in client.get(orders_url("?side=bought")).json()["orders"]] == [
        "5001000000002"
    ]

    # 再补一次：已经记下的刷新一次
    body = client.post(orders_url("/scan")).json()
    assert body["updated"] == ["5001000000001", "5001000000002"]


def test_sync_bought_orders(client, platform):
    platform.orders["5001000000002"] = {
        "item_id": "1001",
        "status": "交易成功",
        "price": 1000,
        "created": 1761800000000,
        "bought": True,
    }
    result = client.post(orders_url("/sync"), json={"side": "bought"}).json()
    assert (result["source"], result["added"]) == ("list", ["5001000000002"])
    [order] = client.get(orders_url("?side=bought")).json()["orders"]
    assert (order["side"], order["peer_nick"], order["price_cent"]) == ("bought", "卖家乙", 1000)
    assert client.post(orders_url("/sync"), json={"side": "both"}).status_code == 400


def drafts_url(path=""):
    return f"/api/accounts/{UNB}/drafts{path}"


def test_draft_to_published_item(client, platform):
    draft = client.post(drafts_url()).json()
    url = drafts_url(f"/{draft['id']}")
    # 请求体就是图片
    resp = client.post(f"{url}/images", content=png(), headers={"content-type": "image/png"})
    assert resp.status_code == 200, resp.text
    [image] = resp.json()["images"]
    assert (image["width"], image["height"], image["url"]) == (80, 60, None)
    resp = client.get(f"{url}/images/{image['sha']}")
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content[:2] == b"\xff\xd8"

    [home, _] = client.get(f"/api/accounts/{UNB}/publish/addresses").json()["addresses"]
    form = {
        "description": "宜家落地灯，九成新",
        "price": "49",
        "post_mode": "fixed",
        "post_price": "8",
        "images": [image["sha"]],
        "address": home["address"],
    }
    draft = client.put(url, json=form).json()
    assert (draft["price_cent"], draft["post_price_cent"]) == (4900, 800)
    assert draft["address"]["poiName"] == "光谷苑"

    draft = client.post(f"{url}/recommend").json()
    assert draft["category"] == "落地灯"
    assert draft["unsupported"] == []
    category = draft["properties"][0]
    assert category["options"][1] == {"key": "126858404", "name": "台灯"}
    choice = {"property_id": "-10000", "keys": ["126858404"]}
    assert client.put(f"{url}/labels", json=choice).json()["category"] == "台灯"

    draft = client.post(f"{url}/publish").json()
    assert draft["item_id"] == "9001"
    assert draft["images"][0]["url"] == "https://img.example/up1.jpg"
    assert platform.published[0]["itemPostFeeDTO"]["postPriceInCent"] == "800"
    assert client.get(items_url()).json()["items"][0]["item_id"] == "9001"
    [op] = client.get(f"/api/accounts/{UNB}/item-ops").json()["ops"]
    assert (op["action"], op["item_id"], op["ok"]) == ("publish", "9001", True)

    resp = client.put(url, json=form)
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid")


def test_draft_word_checks(client, platform):
    draft = client.post(drafts_url()).json()
    url = drafts_url(f"/{draft['id']}")
    assert draft["word_hits"] == []
    assert draft["platform_words"] == {"needed": False, "result": None}

    platform.bad_words.append("代购")
    draft = client.put(url, json={"description": "代购，加我微信"}).json()
    [hit] = draft["word_hits"]
    assert (hit["text"], hit["start"], hit["end"], hit["category"], hit["level"]) == (
        "加我微信",
        3,
        7,
        "contact",
        "strong",
    )
    assert hit["basis"] == "official" and hit["suggest"]
    assert draft["platform_words"]["needed"] is True

    body = client.post(f"{url}/words").json()
    assert body["skipped"] is None
    result = body["draft"]["platform_words"]["result"]
    assert (result["words"], result["forbid"], result["current"]) == (["代购"], True, True)
    assert body["draft"]["platform_words"]["needed"] is False

    # 风控熔断以后只给本地词表的结果，平台检查不发请求、说明没跑
    platform.fail["mtop.idle.pc.idleitem.editDetail"] = "RGV587_ERROR::SM::哎哟喂,被挤爆啦"
    client.get(items_url("/1001/detail"))
    sent = len(platform.calls)
    body = client.post(f"{url}/words", json={"force": True}).json()
    assert "暂停" in body["skipped"]
    assert body["draft"]["word_hits"][0]["text"] == "加我微信"
    assert len(platform.calls) == sent


def test_edit_item_through_draft(client, platform):
    client.post(items_url("/sync"), json={})
    draft = client.post(items_url("/1001/edit")).json()
    assert (draft["edit_item_id"], draft["description"], draft["post_keep"]) == (
        "1001",
        "洗衣机",
        False,
    )
    assert draft["images"][0]["url"] == "http://img.example/1001.jpg"
    assert draft["labels"] == [{"name": "成色", "value": "几乎全新"}]
    assert client.get(drafts_url()).json() == {"drafts": []}

    url = drafts_url(f"/{draft['id']}")
    form = {
        "description": "洗衣机 九成新",
        "price": "100",
        "post_mode": "free",
        "images": [img["sha"] for img in draft["images"]],
        "address": draft["address"],
    }
    client.put(url, json=form)
    resp = client.post(f"{url}/publish")
    assert (resp.status_code, resp.json()["code"]) == (409, "price_guard")
    draft = client.post(f"{url}/publish", json={"force": True}).json()
    assert draft["item_id"] == "1001"
    assert (platform.items["1001"]["title"], platform.items["1001"]["price"]) == (
        "洗衣机 九成新",
        10000,
    )
    [op] = client.get(f"/api/accounts/{UNB}/item-ops").json()["ops"]
    assert (op["action"], op["detail"]["fields"]) == ("edit", ["描述", "价格"])


def test_relist_item_through_publish_draft(client, platform):
    client.post(items_url("/sync"), json={})
    resp = client.post(items_url("/1001/relist"))
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid")  # 还在卖

    draft = client.post(items_url("/1002/relist")).json()
    assert (draft["relist_item_id"], draft["edit_item_id"], draft["description"]) == (
        "1002",
        None,
        "平板",
    )
    assert draft["labels"] == [{"name": "成色", "value": "几乎全新"}]
    assert [d["id"] for d in client.get(drafts_url()).json()["drafts"]] == [draft["id"]]

    draft = client.post(drafts_url(f"/{draft['id']}/publish")).json()
    assert draft["item_id"] == "9001"
    [op] = client.get(f"/api/accounts/{UNB}/item-ops").json()["ops"]
    assert (op["action"], op["item_id"], op["detail"]["from_item"]) == ("relist", "9001", "1002")


def test_relist_item_directly(client, platform):
    client.post(items_url("/sync"), json={})
    draft = client.post(items_url("/1002/relist"), json={"publish": True}).json()
    assert (draft["item_id"], draft["relist_item_id"]) == ("9001", "1002")
    items = {it["item_id"]: it for it in client.get(items_url()).json()["items"]}
    assert items["9001"]["status"] == 0
    [op] = client.get(f"/api/accounts/{UNB}/item-ops").json()["ops"]
    assert (op["action"], op["item_id"]) == ("relist", "9001")


def test_draft_errors(client, platform):
    assert client.get(drafts_url("/999")).json()["code"] == "draft_not_found"
    draft = client.post(drafts_url()).json()
    url = drafts_url(f"/{draft['id']}")
    resp = client.post(f"{url}/images", content=b"hello")
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid")
    resp = client.put(url, json={"price": "abc"})
    assert resp.status_code == 400

    client.post(f"{url}/images", content=png())
    sha = client.get(url).json()["images"][0]["sha"]
    address = {"city": "深圳", "divisionId": 1, "gps": "1,2", "poiId": "p"}
    form = {"description": "台灯", "price": "10", "images": [sha], "address": address}
    client.put(url, json=form)
    client.post(f"{url}/recommend")
    platform.bad_words.append("台灯")
    resp = client.post(f"{url}/publish")
    assert (resp.status_code, resp.json()["code"]) == (409, "publish_blocked")
    assert "违禁词" in client.get(url).json()["error"]

    assert client.delete(url).json() == {"ok": True}
    assert client.get(drafts_url()).json() == {"drafts": []}


def test_other_users_cannot_see_drafts(client, database_url):
    draft = client.post(drafts_url()).json()
    add_user(database_url, "other")
    sign_in(client, "other")
    resp = client.get(drafts_url(f"/{draft['id']}"))
    assert (resp.status_code, resp.json()["code"]) == (404, "account_not_found")


def test_place_search(tmp_path, accounts, platform, database_url, client):
    resp = client.get("/api/publish/places", params={"q": "北京站"})
    assert (resp.status_code, resp.json()["code"]) == (503, "places_unavailable")
    assert client.get(f"/api/accounts/{UNB}/publish/addresses").json()["search"] is False

    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(platform),
        web_dist=None,
        amap_key="k",
    )
    with TestClient(app) as other:
        sign_in(other)
        [place] = other.get("/api/publish/places", params={"q": "北京站"}).json()["places"]
        assert (place["name"], place["address"]["poiId"]) == ("北京站", "B000A7BD6C")
        assert other.get(f"/api/accounts/{UNB}/publish/addresses").json()["search"] is True
        # 搜出来的地址原样存进草稿
        draft = other.post(drafts_url()).json()
        draft = other.put(drafts_url(f"/{draft['id']}"), json={"address": place["address"]}).json()
        assert draft["address"]["gps"] == "39.903030,116.427287"
