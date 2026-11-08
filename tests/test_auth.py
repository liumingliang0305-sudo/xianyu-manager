"""管理后台的登录鉴权（app/auth.py、app/users.py）和用户之间的数据隔离。"""

import asyncio
import os
import re
import time

import httpx
import jwt
import pytest
from fakes import PASSWORD, UNB, add_user, sign_in
from fastapi.testclient import TestClient

from app import auth, cli, db
from app.api import create_app
from app.users import REFRESH_IDLE, AccountTakenError, Users
from xianyu_core.credential import Credential
from xianyu_core.guard import Guard

OTHER = "43"
# 不用登录就能调的接口；别的 /api 接口都要登录
PUBLIC = {"/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/api/health"}


@pytest.fixture
def app(accounts, platform, database_url):
    return create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def users(database):
    return Users(database)


def bearer(token):
    return {"authorization": f"Bearer {token}"}


def test_every_api_needs_sign_in(app, client):
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if path in PUBLIC:
            continue
        url = re.sub(r"\{\w+\}", UNB, path)
        for method in operations:
            resp = client.request(method, url, json={})
            assert (resp.status_code, resp.json()["code"]) == (401, "unauthenticated"), url
            checked += 1
    assert checked > 25
    assert client.get("/api/health").json() == {"ok": True}  # 发版脚本用，不用登录


def test_sign_in_and_wrong_password(client, database_url):
    add_user(database_url, "alice", accounts=[UNB])
    resp = client.post("/api/auth/login", json={"username": " Alice ", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["username"] == "alice"
    assert body["expires_in"] == auth.ACCESS_TTL
    assert client.get("/api/accounts", headers=bearer(body["access_token"])).status_code == 200

    # 刷新令牌在 HttpOnly cookie 里，只发给 /api/auth
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Path=/api/auth" in cookie and "SameSite=strict" in cookie

    for username, password in (("alice", "wrong-password"), ("nobody", PASSWORD)):
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        assert (resp.status_code, resp.json()["code"]) == (401, "login_failed")
        assert resp.json()["message"] == "用户名或密码不对"  # 看不出是用户名还是密码不对


def test_too_many_wrong_passwords_locks_the_username(client, database_url):
    add_user(database_url, "alice")
    for _ in range(auth.MAX_FAILURES):
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"})
    resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert (resp.status_code, resp.json()["code"]) == (429, "too_many_attempts")


def test_too_many_wrong_passwords_from_one_ip(client, database_url):
    add_user(database_url, "alice")
    for n in range(auth.MAX_FAILURES_PER_IP):  # 换着用户名猜
        client.post("/api/auth/login", json={"username": f"guess{n}", "password": "x" * 8})
    resp = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert (resp.status_code, resp.json()["code"]) == (429, "too_many_attempts")


def test_local_clients_are_not_limited_by_ip():
    # SSH 隧道、本机开发进来的都是 127.0.0.1，按 IP 限会互相连累
    assert auth.client_ip("127.0.0.1") is None
    assert auth.client_ip("::1") is None
    assert auth.client_ip(None) is None
    assert auth.client_ip("203.0.113.9") == "203.0.113.9"


def test_security_headers_and_no_api_docs(client):
    resp = client.get("/api/health")
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_refresh_and_sign_out(client, database_url):
    add_user(database_url, accounts=[UNB])
    sign_in(client)
    refresh_token = client.cookies["xym_refresh"]
    del client.headers["authorization"]

    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 200
    assert (
        client.get("/api/accounts", headers=bearer(resp.json()["access_token"])).status_code == 200
    )

    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert "xym_refresh" not in client.cookies
    # 退出以后这个刷新令牌在服务端也删了，留着 cookie 的副本也换不出访问令牌
    client.cookies.set("xym_refresh", refresh_token)
    resp = client.post("/api/auth/refresh")
    assert (resp.status_code, resp.json()["code"]) == (401, "unauthenticated")


def test_refresh_token_expires_when_unused(client, database_url, monkeypatch):
    add_user(database_url)
    sign_in(client)
    later = time.time() + REFRESH_IDLE + 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert client.post("/api/auth/refresh").status_code == 401


def test_expired_or_forged_access_token_is_rejected(client, database_url, auth_key):
    user_id = add_user(database_url, accounts=[UNB])
    sign_in(client)
    expired = jwt.encode(
        {"sub": str(user_id), "ver": 0, "exp": int(time.time()) - 1},
        auth.load_key(auth_key),
        algorithm=auth.ALGORITHM,
    )
    forged = jwt.encode(
        {"sub": str(user_id), "ver": 0, "exp": int(time.time()) + 600},
        b"x" * 32,
        algorithm=auth.ALGORITHM,
    )
    for token in (expired, forged, "garbage"):
        resp = client.get("/api/accounts", headers=bearer(token))
        assert (resp.status_code, resp.json()["code"]) == (401, "unauthenticated")


def test_password_change_signs_out_everywhere_else(client, database_url):
    add_user(database_url, accounts=[UNB])
    other_access = sign_in(client)  # 另一个浏览器上的登录
    other_refresh = client.cookies["xym_refresh"]
    client.cookies.clear()
    sign_in(client)

    resp = client.post(
        "/api/auth/password", json={"old_password": "wrong-password", "new_password": "x" * 8}
    )
    assert (resp.status_code, resp.json()["message"]) == (400, "原密码不对")
    resp = client.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "x"})
    assert resp.status_code == 400

    resp = client.post(
        "/api/auth/password", json={"old_password": PASSWORD, "new_password": "new-password"}
    )
    assert resp.status_code == 200
    # 这个浏览器换了一套新令牌，接着能用
    assert (
        client.get("/api/accounts", headers=bearer(resp.json()["access_token"])).status_code == 200
    )
    assert client.post("/api/auth/refresh").status_code == 200
    # 别处的访问令牌、刷新令牌都作废了
    assert client.get("/api/accounts", headers=bearer(other_access)).status_code == 401
    client.cookies.set("xym_refresh", other_refresh)
    assert client.post("/api/auth/refresh").status_code == 401
    assert sign_in(client, password="new-password")


def test_disabled_user_is_locked_out(client, database_url, users):
    user_id = add_user(database_url, accounts=[UNB])
    token = sign_in(client)
    users.set_disabled(user_id, True)

    assert client.get("/api/accounts", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401
    resp = client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
    assert resp.status_code == 401

    users.set_disabled(user_id, False)
    assert sign_in(client)


def test_users_only_see_their_own_accounts(client, accounts, database_url):
    Credential(cookies={"unb": OTHER, "_m_h5_tk": "tok_1"}).save(accounts.path(OTHER))
    add_user(database_url, "alice", accounts=[UNB])
    add_user(database_url, "bob", accounts=[OTHER])
    sign_in(client, "alice")

    assert [a["id"] for a in client.get("/api/accounts").json()["accounts"]] == [UNB]
    assert client.get(f"/api/accounts/{UNB}/items").status_code == 200

    base = f"/api/accounts/{OTHER}"
    requests = [
        ("GET", f"{base}/items"),
        ("POST", f"{base}/items/sync"),
        ("GET", f"{base}/items/1001/detail"),
        ("POST", f"{base}/items/1001/price"),
        ("DELETE", f"{base}/items/1001"),
        ("GET", f"{base}/item-ops"),
        ("GET", f"{base}/orders"),
        ("GET", f"{base}/orders/5001/detail"),
        ("GET", f"{base}/conversations"),
        ("POST", f"{base}/conversations/777/messages"),
        ("GET", f"{base}/messages/stream"),
        ("POST", f"{base}/check"),
        ("DELETE", f"{base}/block"),
    ]
    bodies = {"items/sync": {}, "price": {"price": "1"}, "messages": {"text": "在的"}}
    for method, url in requests:
        body = next((v for k, v in bodies.items() if url.endswith(k)), None)
        resp = client.request(method, url, json=body)
        assert resp.status_code == 404, url
        # 和不存在的账号返回一样，看不出别人有没有这个账号
        assert resp.json() == {"code": "account_not_found", "message": f"找不到账号 {OTHER}"}
    assert client.get("/api/accounts/999/items").json()["message"] == "找不到账号 999"


def test_claim_and_assign(users):
    alice = run_create(users, "alice")
    bob = run_create(users, "bob")
    users.claim(UNB, alice.id)
    users.claim(UNB, alice.id)  # 再登录一次没事
    with pytest.raises(AccountTakenError):
        users.claim(UNB, bob.id)
    users.assign(UNB, bob.id)
    assert users.account_ids(bob.id) == {UNB}
    assert users.account_ids(alice.id) == set()


@pytest.mark.parametrize("name", ["a", "has space", "x" * 33, "a/b"])
def test_username_rules(users, name):
    with pytest.raises(ValueError, match="用户名"):
        run_create(users, name)


def test_cli_first_user_takes_existing_accounts(accounts, database_url, monkeypatch):
    Credential(cookies={"unb": OTHER}).save(accounts.path(OTHER))
    base = ["--accounts", str(accounts.root), "--db", database_url]
    monkeypatch.setattr("sys.stdin", _Stdin("first-password\nsecond-password\n"))
    assert cli.main([*base, "users", "add", "Alice"]) == 0
    assert cli.main([*base, "users", "add", "bob"]) == 0
    assert cli.main([*base, "users", "add", "bob"]) == 1  # 重名

    conn = db.connect(database_url)
    users = Users(conn)
    alice, bob = users.find("alice"), users.find("bob")
    # 第一个用户接手已有的账号，后来的不接
    assert users.account_ids(alice.id) == {UNB, OTHER}
    assert users.account_ids(bob.id) == set()

    assert cli.main([*base, "users", "disable", "bob"]) == 0
    assert users.find("bob").disabled
    conn.close()


class _Stdin:
    """假的标准输入：不是终端，按行读密码。"""

    def __init__(self, text):
        self._lines = iter(text.splitlines(keepends=True))

    def isatty(self):
        return False

    def readline(self):
        return next(self._lines, "")


def run_create(users, name):
    return asyncio.run(users.create(name, PASSWORD))


def test_xym_command_reads_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("XYM_TEST_FROM_FILE=file\nXYM_TEST_ALREADY_SET=file\n")
    monkeypatch.setattr(cli, "ENV_FILE", env)
    monkeypatch.setattr(cli, "main", lambda: 0)
    monkeypatch.setenv("XYM_TEST_FROM_FILE", "")
    monkeypatch.delenv("XYM_TEST_FROM_FILE")  # 先没有；测完 monkeypatch 再删掉 .env 设的
    monkeypatch.setenv("XYM_TEST_ALREADY_SET", "shell")
    assert cli.entry() == 0
    assert os.environ["XYM_TEST_FROM_FILE"] == "file"
    assert os.environ["XYM_TEST_ALREADY_SET"] == "shell"  # 终端里设了的优先

    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "missing.env")
    assert cli.entry() == 0  # 没有 .env 也照常跑
