import httpx
import pytest
from fakes import add_user, sign_in
from fastapi.testclient import TestClient

from app.api import create_app
from app.prompt_templates import MAX_CONTENT, MAX_NAME, PREVIEW_LENGTH, PromptTemplateService
from xianyu_core.guard import Guard

URL = "/api/prompt-templates"
BODY = {"name": "家电文案", "content": "先写商品名称和卖点。\n\n规格：{商品规格}\n不要编造参数。"}


@pytest.fixture
def client(tmp_path, database_url):
    def no_platform_requests(request):
        pytest.fail(f"模板管理不该请求平台：{request.url}")

    add_user(database_url)
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=tmp_path / "accounts",
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(no_platform_requests),
        interval=0,
        web_dist=None,
    )
    with TestClient(app) as client:
        sign_in(client)
        yield client


def test_crud_without_xianyu_account_or_model(client, database):
    assert client.get(URL).json() == {"templates": []}
    resp = client.post(
        URL, json={"name": f"  {BODY['name']}  ", "content": f"\n {BODY['content']} \n"}
    )
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["name"] == BODY["name"]
    assert first["content"] == BODY["content"]
    assert first["created_at"] == first["updated_at"] > 0
    template_url = f"{URL}/{first['id']}"
    assert client.get(template_url).json() == first

    summary = client.get(URL).json()["templates"][0]
    assert summary["preview"] == BODY["content"]
    assert summary["content_length"] == len(BODY["content"])
    assert "content" not in summary and "user_id" not in summary

    changed = {"name": "精简版", "content": "第一句写商品名称。\n第二句写真实规格。"}
    resp = client.put(template_url, json=changed)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved == {
        **first,
        **changed,
        "updated_at": saved["updated_at"],
    }
    assert saved["updated_at"] >= first["updated_at"]
    # 换一个服务对象也能读回来，正文确实保存在数据库。
    user_id = database.execute("SELECT id FROM users WHERE username = 'tester'").fetchone()["id"]
    assert PromptTemplateService(database).get(user_id, first["id"]).content == changed["content"]

    assert client.delete(template_url).json() == {"ok": True}
    assert client.get(URL).json() == {"templates": []}
    for response in [client.get(template_url), client.delete(template_url)]:
        assert response.status_code == 404
        assert response.json()["code"] == "prompt_template_not_found"


def test_recently_updated_first_with_stable_order_for_ties(client, database):
    first = client.post(URL, json=BODY).json()
    second = client.post(URL, json={**BODY, "name": "客服话术"}).json()
    assert [row["id"] for row in client.get(URL).json()["templates"]] == [second["id"], first["id"]]
    client.put(f"{URL}/{first['id']}", json={**BODY, "content": "修改后的正文"})
    assert client.get(URL).json()["templates"][0]["id"] == first["id"]
    database.execute("UPDATE prompt_templates SET updated_at = 1")
    assert [row["id"] for row in client.get(URL).json()["templates"]] == [second["id"], first["id"]]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": " \n\t"}, "模板名称不能为空"),
        ({"content": " \n\t\u3000"}, "模板内容不能为空"),
        ({"name": "名" * (MAX_NAME + 1)}, f"模板名称最多 {MAX_NAME} 字"),
        ({"content": "字" * (MAX_CONTENT + 1)}, f"模板内容最多 {MAX_CONTENT} 字"),
    ],
)
def test_invalid_create_and_update_leave_saved_template_unchanged(client, change, message):
    saved = client.post(URL, json=BODY).json()
    path = f"{URL}/{saved['id']}"
    invalid = {**BODY, **change}
    for response in [client.post(URL, json=invalid), client.put(path, json=invalid)]:
        assert response.status_code == 400
        assert response.json() == {"code": "invalid", "message": message}
    assert client.get(path).json() == saved
    assert len(client.get(URL).json()["templates"]) == 1


def test_length_boundaries_count_unicode_characters_and_list_only_has_preview(client):
    content = "商品🐟\n" * (MAX_CONTENT // 4)
    resp = client.post(URL, json={"name": "🐟" * MAX_NAME, "content": content[:-1] + "。"})
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert len(saved["name"]) == MAX_NAME and len(saved["content"]) == MAX_CONTENT
    summary = client.get(URL).json()["templates"][0]
    assert summary["content_length"] == MAX_CONTENT
    assert summary["preview"] == saved["content"][:PREVIEW_LENGTH]
    assert client.get(f"{URL}/{saved['id']}").json()["content"] == saved["content"]


def test_templates_are_private_to_their_user(client, database_url):
    saved = client.post(URL, json=BODY).json()
    path = f"{URL}/{saved['id']}"
    add_user(database_url, username="other")
    sign_in(client, username="other")
    assert client.get(URL).json() == {"templates": []}
    for response in [
        client.get(path),
        client.put(path, json={"name": "改别人的", "content": "不该保存"}),
        client.delete(path),
    ]:
        assert response.status_code == 404
        assert response.json()["code"] == "prompt_template_not_found"
    own = client.post(URL, json={"name": "自己的模板", "content": "自己的内容"}).json()
    assert [row["id"] for row in client.get(URL).json()["templates"]] == [own["id"]]
    sign_in(client)
    assert client.get(path).json() == saved
    assert [row["id"] for row in client.get(URL).json()["templates"]] == [saved["id"]]


def test_all_template_routes_require_sign_in(client):
    saved = client.post(URL, json=BODY).json()
    path = f"{URL}/{saved['id']}"
    client.headers.pop("authorization")
    for response in [
        client.get(URL),
        client.post(URL, json=BODY),
        client.get(path),
        client.put(path, json=BODY),
        client.delete(path),
    ]:
        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
    sign_in(client)
    assert client.get(path).json() == saved


def test_deleting_user_also_deletes_their_templates(client, database):
    client.post(URL, json=BODY)
    database.execute("DELETE FROM users WHERE username = 'tester'")
    assert database.execute("SELECT count(*) AS n FROM prompt_templates").fetchone()["n"] == 0
