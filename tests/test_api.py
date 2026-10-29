import httpx
import pytest
from fakes import UNB
from fastapi.testclient import TestClient

from app.api import create_app


@pytest.fixture
def client(tmp_path, accounts, platform):
    app = create_app(
        db_path=":memory:",
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
    )
    with TestClient(app) as client:
        yield client


def items_url(path=""):
    return f"/api/accounts/{UNB}/items{path}"


def test_accounts_and_sync_then_list(client):
    assert [a["id"] for a in client.get("/api/accounts").json()["accounts"]] == [UNB]
    assert client.get(items_url()).json() == {"items": [], "last_synced_at": None}

    result = client.post(items_url("/sync"), json={"stats": True}).json()
    assert sorted(result["added"]) == ["1001", "1002"]

    body = client.get(items_url()).json()
    assert body["last_synced_at"] is not None
    by_id = {it["item_id"]: it for it in body["items"]}
    assert by_id["1001"]["price_cent"] == 35800
    assert by_id["1001"]["browse_count"] == 100
    assert [it["item_id"] for it in client.get(items_url("?status=1")).json()["items"]] == ["1002"]


def test_detail_hides_raw_response(client):
    detail = client.get(items_url("/1001/detail")).json()
    assert detail["price_cent"] == 35800
    assert "raw" not in detail


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


def test_unknown_account_is_404(client):
    resp = client.get("/api/accounts/999/items")
    assert resp.status_code == 404
    assert resp.json()["code"] == "account_not_found"


def test_desc_downshelf_delete(client, platform):
    client.post(items_url("/sync"), json={})
    assert client.put(items_url("/1001/desc"), json={"desc": " 新描述 "}).status_code == 200
    assert platform.items["1001"]["title"] == "新描述"
    assert client.post(items_url("/1001/downshelf")).status_code == 200
    assert client.delete(items_url("/1002")).status_code == 200
    assert client.get(items_url()).json()["items"] == []
    gone = client.get(items_url("?include_gone=true")).json()["items"]
    assert [(it["item_id"], it["on_platform"]) for it in gone] == [("1001", False)]


def test_serves_built_frontend_with_spa_fallback(tmp_path, accounts):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "main.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("secret")

    app = create_app(db_path=":memory:", accounts_dir=accounts.root, web_dist=dist)
    with TestClient(app) as client:
        assert client.get("/").text == "<html>app</html>"
        assert client.get("/items/1001").text == "<html>app</html>"
        assert client.get("/assets/main.js").text == "console.log(1)"
        assert client.get("/favicon.svg").text == "<svg/>"
        assert client.get("/api/nope").status_code == 404
        assert "secret" not in client.get("/..%2Fsecret.txt").text
