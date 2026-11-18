"""问题反馈（app/feedback.py）：提交、按用户隔离、管理员处理、推成 GitHub issue。"""

import json

import httpx
import pytest
from fakes import PASSWORD, add_user, sign_in
from fastapi.testclient import TestClient

from app import db
from app.api import create_app
from app.feedback import MAX_CONTENT, MAX_TITLE, load_github
from app.users import Users
from xianyu_core.guard import Guard

URL = "/api/feedback"
REPO = "owner/xianyu-manager"
BODY = {
    "kind": "bug",
    "title": "订单页同步按钮没反应",
    "content": "点了「同步」以后一直转圈。\n刷新页面也一样。",
    "page": "/orders",
}


class FakeGitHub:
    """假的 GitHub 接口：记下建 issue 的请求，按 status 回。"""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 201
        self.number = 41

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.github.com":
            pytest.fail(f"问题反馈不该请求别的地方：{request.url}")
        self.requests.append(request)
        if self.status != 201:
            return httpx.Response(self.status, json={"message": "Bad credentials"})
        self.number += 1
        return httpx.Response(
            201,
            json={
                "number": self.number,
                "html_url": f"https://github.com/{REPO}/issues/{self.number}",
            },
        )


@pytest.fixture
def github():
    return FakeGitHub()


def make_admin(database_url, username):
    user_id = add_user(database_url, username)
    conn = db.connect(database_url)
    Users(conn).set_admin(user_id, True)
    conn.close()
    return user_id


@pytest.fixture
def make_client(tmp_path, database_url, github):
    clients = []

    def make(username="tester", *, admin=False, token="ghp_test", repo=REPO):
        if admin:
            make_admin(database_url, username)
        else:
            add_user(database_url, username)
        app = create_app(
            database_url=database_url,
            guard=Guard(),
            accounts_dir=tmp_path / "accounts",
            images_dir=tmp_path / "images",
            transport=httpx.MockTransport(github),
            interval=0,
            web_dist=None,
            github_token=token,
            github_repo=repo,
        )
        client = TestClient(app, headers={"user-agent": "Mozilla/5.0 Test"})
        client.__enter__()
        clients.append(client)
        sign_in(client, username)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def test_submit_and_list_own(make_client):
    client = make_client()
    assert client.get(URL).json() == {"feedback": []}
    resp = client.post(
        URL, json={**BODY, "title": f"  {BODY['title']} ", "content": f"\n{BODY['content']}  "}
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["kind"] == "bug"
    assert created["title"] == BODY["title"]
    assert created["content"] == BODY["content"]
    assert created["page"] == "/orders"
    assert created["user_agent"] == "Mozilla/5.0 Test"
    assert created["username"] == "tester"
    assert created["status"] == "open"
    assert created["issue_number"] is None and created["issue_url"] is None
    assert created["created_at"] == created["updated_at"] > 0
    assert "user_id" not in created

    second = client.post(URL, json={**BODY, "kind": "idea", "title": "想要批量改价"}).json()
    # 新提交的在前
    assert [f["id"] for f in client.get(URL).json()["feedback"]] == [second["id"], created["id"]]
    assert client.get(f"{URL}/{created['id']}").json() == created


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "praise"}, "种类"),
        ({"title": "   "}, "标题"),
        ({"title": "长" * (MAX_TITLE + 1)}, "标题"),
        ({"content": "\n "}, "具体情况"),
        ({"content": "长" * (MAX_CONTENT + 1)}, "具体情况"),
    ],
)
def test_rejects_bad_input(make_client, change, message):
    client = make_client()
    resp = client.post(URL, json={**BODY, **change})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"
    assert message in resp.json()["message"]
    assert client.get(URL).json() == {"feedback": []}


def test_users_only_see_their_own(make_client):
    alice = make_client("alice")
    bob = make_client("bob")
    mine = alice.post(URL, json=BODY).json()
    assert bob.get(URL).json() == {"feedback": []}
    for resp in [bob.get(f"{URL}/{mine['id']}"), bob.delete(f"{URL}/{mine['id']}")]:
        assert resp.status_code == 404
        assert resp.json()["code"] == "feedback_not_found"
    # 不是管理员看不了所有人的、改不了状态、推不了
    for resp in [
        bob.get(URL, params={"scope": "all"}),
        alice.get(URL, params={"scope": "all"}),
        alice.put(f"{URL}/{mine['id']}/status", json={"status": "done"}),
        alice.get(f"{URL}/{mine['id']}/issue"),
        alice.post(f"{URL}/{mine['id']}/issue", json={}),
    ]:
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden"
    # 自己的能删
    assert alice.delete(f"{URL}/{mine['id']}").json() == {"ok": True}
    assert alice.get(URL).json() == {"feedback": []}


def test_admin_sees_everyone_and_marks_done(make_client):
    alice = make_client("alice")
    admin = make_client("boss", admin=True)
    submitted = alice.post(URL, json=BODY).json()
    own = admin.post(URL, json={**BODY, "kind": "other", "title": "自己记一条"}).json()

    everyone = admin.get(URL, params={"scope": "all"}).json()
    assert [f["id"] for f in everyone["feedback"]] == [own["id"], submitted["id"]]
    assert everyone["feedback"][1]["username"] == "alice"
    assert everyone["github"] == {"repo": REPO, "missing": None}
    # 管理员自己提交的照样在"我提交的"里
    assert [f["id"] for f in admin.get(URL).json()["feedback"]] == [own["id"]]
    assert admin.get(f"{URL}/{submitted['id']}").json() == submitted

    done = admin.put(f"{URL}/{submitted['id']}/status", json={"status": "done"}).json()
    assert done["status"] == "done"
    assert done["updated_at"] >= submitted["updated_at"]
    assert alice.get(f"{URL}/{submitted['id']}").json()["status"] == "done"
    resp = admin.put(f"{URL}/{submitted['id']}/status", json={"status": "closed"})
    assert resp.status_code == 400
    resp = admin.put(f"{URL}/999999/status", json={"status": "done"})
    assert resp.status_code == 404

    # 管理员能删别人的
    assert admin.delete(f"{URL}/{submitted['id']}").json() == {"ok": True}
    assert alice.get(URL).json() == {"feedback": []}


def test_push_to_github(make_client, github):
    alice = make_client("alice")
    admin = make_client("boss", admin=True)
    submitted = alice.post(URL, json=BODY).json()
    issue_url = f"{URL}/{submitted['id']}/issue"

    draft = admin.get(issue_url).json()
    assert draft["repo"] == REPO and draft["missing"] is None
    assert draft["title"] == BODY["title"]
    assert draft["labels"] == ["bug"]
    assert draft["body"].startswith(BODY["content"])
    assert "`/orders`" in draft["body"]
    assert "Mozilla/5.0 Test" in draft["body"]
    assert f"#{submitted['id']}" in draft["body"]
    # 公开仓库谁都看得到：默认正文不带提交人
    assert "alice" not in draft["body"]
    assert not github.requests  # 看默认正文不请求 GitHub

    resp = admin.post(issue_url, json={"title": "  订单同步卡住 ", "body": "改过的正文\n"})
    assert resp.status_code == 200, resp.text
    pushed = resp.json()
    assert pushed["issue_number"] == 42
    assert pushed["issue_url"] == f"https://github.com/{REPO}/issues/42"
    assert pushed["pushed_at"] > 0
    # 推了不等于处理完了
    assert pushed["status"] == "open"
    # 提交人也看得到推到了哪个 issue
    assert alice.get(f"{URL}/{submitted['id']}").json()["issue_number"] == 42

    (request,) = github.requests
    assert request.method == "POST"
    assert str(request.url) == f"https://api.github.com/repos/{REPO}/issues"
    assert request.headers["authorization"] == "Bearer ghp_test"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert json.loads(request.content) == {
        "title": "订单同步卡住",
        "body": "改过的正文",
        "labels": ["bug"],
    }

    # 一条只推一次
    resp = admin.post(issue_url, json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_pushed"
    assert len(github.requests) == 1


def test_push_uses_default_draft_and_kind_label(make_client, github):
    admin = make_client("boss", admin=True)
    idea = admin.post(URL, json={**BODY, "kind": "idea", "page": ""}).json()
    draft = admin.get(f"{URL}/{idea['id']}/issue").json()
    assert draft["labels"] == ["enhancement"]
    assert "- 页面：" not in draft["body"]
    assert admin.post(f"{URL}/{idea['id']}/issue", json={}).status_code == 200
    sent = json.loads(github.requests[0].content)
    assert sent == {"title": draft["title"], "body": draft["body"], "labels": ["enhancement"]}

    resp = admin.post(f"{URL}/{idea['id']}/issue", json={"title": " "})
    assert resp.status_code == 409  # 已经推过，先拦住


def test_push_rejects_empty_title(make_client, github):
    admin = make_client("boss", admin=True)
    created = admin.post(URL, json=BODY).json()
    resp = admin.post(f"{URL}/{created['id']}/issue", json={"title": " ", "body": "x"})
    assert resp.status_code == 400
    assert not github.requests


def test_github_errors_leave_feedback_unpushed(make_client, github):
    admin = make_client("boss", admin=True)
    created = admin.post(URL, json=BODY).json()
    github.status = 401
    resp = admin.post(f"{URL}/{created['id']}/issue", json={})
    assert resp.status_code == 502
    assert resp.json()["code"] == "github_error"
    assert "token" in resp.json()["message"] and "Bad credentials" in resp.json()["message"]
    assert admin.get(f"{URL}/{created['id']}").json()["issue_number"] is None

    # 修好了能再推
    github.status = 201
    assert admin.post(f"{URL}/{created['id']}/issue", json={}).json()["issue_number"] == 42


def test_github_not_configured(make_client, github):
    admin = make_client("boss", admin=True, token=None, repo="not a repo")
    created = admin.post(URL, json=BODY).json()
    github_info = admin.get(URL, params={"scope": "all"}).json()["github"]
    assert github_info["repo"] is None
    assert "XYM_GITHUB_TOKEN" in github_info["missing"]
    # 默认正文照样能看，推不了
    assert admin.get(f"{URL}/{created['id']}/issue").json()["missing"] == github_info["missing"]
    resp = admin.post(f"{URL}/{created['id']}/issue", json={})
    assert resp.status_code == 503
    assert resp.json()["code"] == "github_unavailable"
    assert not github.requests


def test_login_tells_page_who_is_admin(make_client):
    for username, admin in [("boss", True), ("alice", False)]:
        client = make_client(username, admin=admin)
        resp = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert resp.json()["user"]["is_admin"] is admin


def test_load_github(monkeypatch):
    monkeypatch.delenv("XYM_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("XYM_GITHUB_REPO", raising=False)
    assert load_github() == (None, None)
    monkeypatch.setenv("XYM_GITHUB_TOKEN", " ghp_x \n")
    monkeypatch.setenv("XYM_GITHUB_REPO", REPO)
    assert load_github() == ("ghp_x", REPO)
