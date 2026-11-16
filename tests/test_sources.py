import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from fakes import UNB, add_user, png, sign_in
from fastapi.testclient import TestClient
from test_agent import FakeModel, send

from app import sources
from app.api import create_app
from app.sources import PublicTransport, canonical_url, extract, fetch, image_url, public_url
from xianyu_core.guard import Guard
from xianyu_core.publish import MAX_DESC, strip_emoji

URL = "https://www.jianpian.cn/a/1g2eyk37"
ARTICLE = json.loads((Path(__file__).parent / "jianpian_article.json").read_text())


def article_html(state=None):
    return (
        '<html><head><title>简篇</title></head><body><img src="/avatar.jpg">'
        "<script>window.__INITIAL_STATE__="
        + json.dumps(state if state is not None else ARTICLE)
        + ";</script></body></html>"
    )


def test_extract_real_jianpian_article():
    source = extract(article_html(), URL + "?sc=groupmessage&sd=1&a_uid=7149062#share")
    assert source.url == URL
    assert source.platform == "简篇"
    assert source.author == "广东漫途精品民宿度假别墅"
    assert source.title.startswith("新品-「栖云·庄园」从化高端")
    assert len(source.images) == 48 and len(source.content) == 1255
    assert "民宿：16房30床（全套间）\n床型分布：" in source.content
    assert "404房：1.5床×2张（独立卫生间)-带大露台" in source.content
    assert "<p" not in source.content and "去App下载" not in source.content
    assert all(img.url.startswith("https://img-volc.jianpian.info/") for img in source.images)
    assert source.images[0].width == 2000
    assert extract(article_html(), URL).source_id == source.source_id
    assert canonical_url("https://jianpian.cn/a/1g2eyk37") == URL


def test_generic_article_text_lazy_relative_images_and_deduplication():
    html = """<html><head><title>山间民宿</title><meta name="author" content="旅宿供应商"></head>
    <body><nav>首页导航<img src="/nav.png"></nav><article>
    <h1>山间民宿</h1><p>两间卧室<br>独立庭院 &amp; 厨房</p><p>可住<b>四人</b>。</p>
    <script>恶意脚本</script><div hidden>隐藏内容</div><button>点赞</button>
    <img data-src="/room.jpg?signature=abc" width="800" height="600">
    <img src="/room.jpg?signature=abc"><img src="//cdn.example/yard.webp">
    <img src="data:image/png;base64,abc"><img src="javascript:alert(1)">
    </article><footer>打开 App</footer></body></html>"""
    source = extract(html, "https://supplier.example/stay")
    assert source.title == "山间民宿" and source.author == "旅宿供应商"
    assert source.content == "山间民宿\n两间卧室\n独立庭院 & 厨房\n可住四人。"
    assert [img.url for img in source.images] == [
        "https://supplier.example/room.jpg?signature=abc",
        "https://cdn.example/yard.webp",
    ]
    assert source.images[0].width == 800


def test_image_query_parameters_are_preserved():
    url = "https://img-volc.jianpian.info/a.jpg?imageMogr2/thumbnail/800x&signature=a%2Bb&sd=1"
    assert image_url(url, URL) == url


def test_public_transport_pins_ip_keeps_tls_hostname_and_separates_origins(monkeypatch):
    pools = []
    seen = []

    def pool(**kwargs):
        transport = httpx.MockTransport(
            lambda request: (
                seen.append(
                    (str(request.url), request.headers["host"], request.extensions["sni_hostname"])
                )
                or httpx.Response(200)
            )
        )
        pools.append(transport)
        return transport

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", pool)

    async def run():
        async with httpx.AsyncClient(transport=PublicTransport()) as client:
            for url in [
                "https://first.example/a",
                "https://first.example/b",
                "https://second.example/c",
            ]:
                await client.get(url, extensions={"source_ip": "93.184.216.34"})

    asyncio.run(run())
    assert len(pools) == 2
    assert seen == [
        ("https://93.184.216.34/a", "first.example", "first.example"),
        ("https://93.184.216.34/b", "first.example", "first.example"),
        ("https://93.184.216.34/c", "second.example", "second.example"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/1",
        "javascript:alert(1)",
        "https://user:password@example.com/",
        "https://example.com:9999/",
        "not-a-url",
    ],
)
def test_rejects_invalid_urls(url):
    with pytest.raises(ValueError):
        canonical_url(url)


@pytest.mark.parametrize(
    ("url", "ip", "allowed"),
    [
        ("https://example.com", "93.184.216.34", True),
        ("http://127.0.0.1", "127.0.0.1", False),
        ("https://internal.example", "10.0.0.1", False),
        ("http://169.254.169.254/latest", "169.254.169.254", False),
        ("https://example.com", "::1", False),
        ("https://www.jianpian.cn/a/1", "127.0.0.1", False),
        ("https://www.jianpian.cn/a/1", "198.18.0.57", True),
        ("https://img-volc.jianpian.info/a.jpg", "198.18.0.58", True),
        ("https://www.jianpian.cn.evil.example/a/1", "198.18.0.57", False),
        ("http://www.jianpian.cn/a/1", "198.18.0.57", False),
    ],
)
def test_public_dns_and_proxy_boundaries(monkeypatch, url, ip, allowed):
    monkeypatch.setattr(sources, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (ip, 443))])
    if allowed:
        asyncio.run(public_url(url))
    else:
        with pytest.raises(ValueError, match="公开网页"):
            asyncio.run(public_url(url))


def test_redirect_cannot_access_private_host(monkeypatch):
    def resolve(host, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", 443))]

    monkeypatch.setattr(sources, "getaddrinfo", resolve)
    requests = []

    def route(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://localhost/private"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            with pytest.raises(ValueError, match="公开网页"):
                await fetch(client, "https://example.com", max_bytes=100)

    asyncio.run(run())
    assert requests == ["https://example.com/"]


@pytest.mark.parametrize(
    ("kind", "content", "message"),
    [
        ("application/pdf", b"pdf", "没有返回网页"),
        ("text/html", b"x" * 101, "网页过大"),
    ],
)
def test_fetch_response_type_and_size_limits(monkeypatch, kind, content, message):
    monkeypatch.setattr(
        sources, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, headers={"content-type": kind})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match=message):
                await fetch(client, "https://example.com/", max_bytes=100)

    asyncio.run(run())


class FakeSources:
    def __init__(self):
        self.state = copy.deepcopy(ARTICLE)
        self.broken = set()
        self.downloads = []
        self.image = png()

    def __call__(self, request):
        url = str(request.url)
        if request.url.host == "www.jianpian.cn":
            return httpx.Response(
                200,
                text=article_html(self.state),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if request.url.host == "img-volc.jianpian.info":
            self.downloads.append(url)
            return httpx.Response(
                404 if url in self.broken else 200,
                content=self.image,
                headers={"content-type": "image/png"},
            )
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def source_server(monkeypatch):
    monkeypatch.setattr(
        sources, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )
    return FakeSources()


@pytest.fixture
def client(tmp_path, accounts, database_url, source_server, model):
    app = create_app(
        database_url=database_url,
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(source_server),
        interval=0,
        web_dist=None,
        guard=Guard(),
        agent_model=model.model,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        yield client


def imported(client):
    response = client.post("/api/sources", json={"url": URL + "?sc=groupmessage&sd=1"})
    assert response.status_code == 200, response.text
    return response.json()["source"]


def test_import_save_read_and_update_without_redownloading(client, source_server):
    source = imported(client)
    assert source["image_count"] == 48 and source["missing_images"] == 0
    assert source["content_length"] == 1255 and source["cover_sha"]
    source_id = source["source_id"]
    assert len(source_server.downloads) == 48
    listing = client.get("/api/sources").json()["sources"]
    assert len(listing) == 1 and "content" not in listing[0] and "images" not in listing[0]
    assert client.get(f"/api/sources/{source_id}").json()["content"] == source["content"]
    image = client.get(f"/api/sources/{source_id}/images/{source['cover_sha']}")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/sources/{source_id}/images/{'a' * 64}").status_code == 404

    source_server.state["detail"]["article"]["title"] = "栖云庄园 · 新说明"
    response = client.post("/api/sources", json={"url": URL + "?a_uid=123&sc=other"}).json()
    assert response["added"] is False
    assert response["source"]["source_id"] == source_id
    assert response["source"]["title"] == "栖云庄园 · 新说明"
    assert response["source"]["imported_at"] == source["imported_at"]
    assert len(source_server.downloads) == 48


def test_failed_images_are_saved_as_pending_and_retry_only_missing(client, source_server):
    first = extract(article_html(), URL).images[0].url
    source_server.broken.add(first)
    source = imported(client)
    assert source["missing_images"] == 1 and source["images"][0]["sha"] is None
    assert source["content_length"] == 1255
    source_server.broken.clear()
    source_server.downloads.clear()
    retry = client.post(f"/api/sources/{source['source_id']}/images").json()
    assert retry["missing_images"] == 0 and retry["images"][0]["sha"]
    assert source_server.downloads == [first]


def test_owned_sources_and_images_only_and_plugin_cannot_import(client, database_url):
    source = imported(client)
    source_id = source["source_id"]
    token = client.post("/api/auth/plugin-token").json()["token"]
    assert (
        client.post(
            "/api/sources", json={"url": URL}, headers={"authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    add_user(database_url, username="other")
    sign_in(client, username="other")
    assert client.get("/api/sources").json()["sources"] == []
    path = f"/api/sources/{source_id}"
    assert client.get(path).status_code == 404
    assert client.get(f"{path}/images/{source['cover_sha']}").status_code == 404
    assert client.post(f"{path}/images").status_code == 404
    assert client.delete(path).status_code == 404
    response = client.post("/api/agent/chats", json={"source_id": source_id})
    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


def test_draft_reuses_saved_images_and_delete_preserves_shared_files(client, tmp_path):
    source = imported(client)
    source_id = source["source_id"]
    response = client.post(f"/api/accounts/{UNB}/sources/{source_id}/draft")
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["description"].startswith(strip_emoji(source["title"]))
    assert strip_emoji(draft["description"]) == draft["description"]
    assert client.get(f"/api/sources/{source_id}").json()["content"] == source["content"]
    assert len(draft["description"]) <= MAX_DESC
    assert len(draft["images"]) == 9
    assert draft["images"][0]["sha"] == source["images"][0]["sha"]
    image_path = tmp_path / "images" / f"{source['cover_sha']}.jpg"
    assert client.delete(f"/api/sources/{source_id}").status_code == 200
    assert image_path.exists()
    assert client.get(f"/api/accounts/{UNB}/drafts/{draft['id']}").status_code == 200
    assert client.delete(f"/api/accounts/{UNB}/drafts/{draft['id']}").status_code == 200
    assert not image_path.exists()


def test_source_chat_context_is_framed_refreshed_and_deleted_independently(
    client, source_server, model
):
    source = imported(client)
    source_id = source["source_id"]
    chat = client.post("/api/agent/chats", json={"source_id": source_id}).json()
    assert chat["source_id"] == source_id and chat["source"]["title"] == source["title"]
    assert client.get("/api/agent/chats").json()["chats"][0]["source_id"] == source_id
    send(client, chat["id"], "写一段民宿文案")
    instructions, _ = model.calls[-1]
    assert f'<third_party_source source_id="{source_id}">' in instructions
    assert "民宿：16房30床（全套间）" in instructions and "图片 48 张（看不到）" in instructions

    source_server.state["detail"]["article"]["content"][0]["text"] = "<p>更新后的配置说明</p>"
    imported(client)
    send(client, chat["id"], "现在的配置呢")
    assert "更新后的配置说明" in model.calls[-1][0]

    client.delete(f"/api/sources/{source_id}")
    detail = client.get(f"/api/agent/chats/{chat['id']}").json()
    assert (
        detail["source_id"] == source_id
        and detail["source"] is None
        and len(detail["messages"]) == 4
    )
    send(client, chat["id"], "继续")
    assert "第三方货源已经被卖家删掉" in model.calls[-1][0]
    assert "<third_party_source source_id=" not in model.calls[-1][0]


def test_shared_source_image_is_kept_until_last_source_is_removed(client, tmp_path):
    first = imported(client)
    second = client.post("/api/sources", json={"url": URL + "?version=2"}).json()["source"]
    assert first["source_id"] != second["source_id"]
    path = tmp_path / "images" / f"{first['cover_sha']}.jpg"
    client.delete(f"/api/sources/{first['source_id']}")
    assert path.exists()
    client.delete(f"/api/sources/{second['source_id']}")
    assert not path.exists()
