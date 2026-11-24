import asyncio
import copy
import gzip
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fakes import UNB, add_user, png, sign_in
from fastapi.testclient import TestClient
from test_agent import FakeModel, send

from app import webpage
from app.api import create_app
from app.source_parsers import match_url, parse_article, parse_generic, ziwoyou_product
from app.webpage import PublicTransport, canonical_url, fetch, image_url, public_url
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
    source = parse_article(article_html(), URL + "?sc=groupmessage&sd=1&a_uid=7149062#share")
    assert source.url == URL
    assert source.site == "简篇"
    assert source.author == "广东漫途精品民宿度假别墅"
    assert source.title.startswith("新品-「栖云·庄园」从化高端")
    assert len(source.images) == 48 and len(source.content) == 1255
    assert "民宿：16房30床（全套间）\n床型分布：" in source.content
    assert "404房：1.5床×2张（独立卫生间)-带大露台" in source.content
    assert "<p" not in source.content and "去App下载" not in source.content
    assert all(img.url.startswith("https://img-volc.jianpian.info/") for img in source.images)
    assert source.images[0].width == 2000
    assert parse_article(article_html(), URL).url == source.url
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
    source = parse_generic(html, "https://supplier.example/stay")
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
        ("https://d.xiumius.cn/board/v6/a/b", "198.18.0.168", True),
        ("https://task-api.zowoyoo.com/api/x", "198.18.0.175", True),
        ("http://gym.navo.top/wx_msgs/spview/1/2", "198.18.0.171", False),
    ],
)
def test_public_dns_and_proxy_boundaries(monkeypatch, url, ip, allowed):
    monkeypatch.setattr(webpage, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (ip, 443))])
    if allowed:
        asyncio.run(public_url(url))
    else:
        with pytest.raises(ValueError, match="公开网页"):
            asyncio.run(public_url(url))


def test_redirect_cannot_access_private_host(monkeypatch):
    def resolve(host, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", 443))]

    monkeypatch.setattr(webpage, "getaddrinfo", resolve)
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
        webpage, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )

    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, headers={"content-type": kind})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match=message):
                await fetch(client, "https://example.com/", max_bytes=100)

    asyncio.run(run())


XIUMI_SHARE = "https://v.xiumius.cn/board/v6/1IWxoi/2J5dyn"
XIUMI_PAGE = "https://d.xiumius.cn/board/v6/1IWxoi/2J5dyn"
XIUMI_INFO = {
    "show_id": 737808353,
    "title": "温泉度假村",
    "show_data_url": "//sd.xiumius.cn/xmi/pd/3bAQh/abc/1795059953835.json?_ver=1",
    "show_url": XIUMI_SHARE,
    "owner": {"nickname": "文案"},
    "cover": "https://img.xiumi.us/xmi/ua/cover.jpg",
}
XIUMI_SHOW = {
    "title": "【双十一】温泉度假村",
    "cubes": [
        {
            "pages": [
                {
                    "layers": [
                        {
                            "_comp": {
                                "_$raHTML": "<section><p>何以解忧，唯有温泉！</p>"
                                '<p>龙门十三汤</p><img class="raw-image"'
                                ' src="//img.xiumi.us/xmi/ua/a.jpg?x-oss-process=style/xmwebp">'
                                "<p>森林氧吧泡池房</p>"
                                '<img src="//img.xiumi.us/xmi/ua/b.png?x-oss-process=style/xmwebp">'
                                "</section>"
                            }
                        }
                    ]
                }
            ],
            "grounds": [
                {
                    "layers": [
                        {"_comp": {"_$raHTML": '<p>背景层</p><img src="//img.xiumi.us/bg.jpg">'}}
                    ]
                }
            ],
        }
    ],
}
LELEKU = "https://s.leleku.cn/37TkMJ6Vn"
ZIWOYOU_ROUTE = (
    "http://jcjq.ziwoyou.net/m2/#/productdetail/50946091"
    "?treeId=12&infoId=50946091&navigationKey=2bcd6c56&userId=zxzad&orderCustId=6813241"
)
ZIWOYOU_DETAIL = {
    "infoId": "50946091",
    "infoTitle": "【买一送一】￥1280起抢住云浮翔顺金水台温泉小镇",
    "infoTitleDesc": "",
    "salePrice": 1280,
    "price": 1030.0,
    "marketPrice": 1499,
    "unit": "套",
    "areaName": "云浮",
    "viewNames": [{"name": "翔顺金水台莲花温泉", "address": "广东省云浮市新兴县"}],
    "pictures": ["http://qnimg.zowoyoo.com/img/2529346/main.jpg"],
    "contentImgs": [],
    "chargeInclude": "",
    "refundNote": "<p>购买后不设退款</p>",
    "signImg": "http://qnimg.zowoyoo.com/img/2529346/main.jpg",
}
ZIWOYOU_CONTENT = (
    '<section><p>寻一处温泉，置身其中</p><img class="raw-image" class="lazy"'
    ' src="http://qnimg.zowoyoo.com/img/prod/2529346/1.jpeg" data-w="790"></section>'
)
SPVIEW = "http://gym.navo.top/wx_msgs/spview/13340596/4100236"
EDITOR135_DATA = {
    "id": "4100236",
    "name": "【简介】新兴翔顺象窝酒店",
    "summary": "离开城市的钢筋水泥",
    "author": None,
    "content": '<section class="_135editor"><p>离开城市的钢筋水泥<br>与土地、树木近距离接触</p>'
    '<img class="raw-image" src="http://qnimg.zowoyoo.com/img/prod/2529346/2.gif" lazy="loaded">'
    "<p>象窝山生态园</p></section>",
}


def xiumi_page():
    encoded = quote(json.dumps(XIUMI_INFO, ensure_ascii=False), safe="")
    return (
        '<html><head><title>温泉度假村</title><meta property="og:site_name" content="秀米">'
        '<meta property="og:image" content="https://img.xiumi.us/xmi/ua/cover.jpg"></head>'
        '<body><script id="__pre_data">window.injectedData = window.injectedData || {};'
        f'injectedData.showInfo = JSON.parse(decodeURIComponent("{encoded}"));</script>'
        '<div class="tn-board-foot">阅读 投诉</div></body></html>'
    )


def editor135_page():
    return (
        "<html><head><title>【简介】新兴翔顺象窝酒店</title>"
        '<script src="https://static.135editor.com/multi/js/fixmedia.js"></script></head>'
        '<body><div id="main-content"><img src="https://bdn.135editor.com/files/logo.jpg">'
        '<div id="fullpage" class="msg-content"></div></div><script>var BASEURL = "";'
        f"var data = {json.dumps(EDITOR135_DATA)};"
        "$('#fullpage').html(data.content);</script></body></html>"
    )


def html_response(text):
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


def json_response(body, headers=None, *, compress=False):
    raw = json.dumps(body, ensure_ascii=False).encode()
    extra = {"content-encoding": "gzip"} if compress else {}
    return httpx.Response(
        200,
        content=gzip.compress(raw) if compress else raw,
        headers={"content-type": "application/json;charset=UTF-8", **extra, **(headers or {})},
    )


class FakeSources:
    def __init__(self):
        self.state = copy.deepcopy(ARTICLE)
        self.broken = set()
        self.downloads = []
        self.requests = []
        self.logins = []
        self.image = png()
        self.distinct = False  # True 时每个图片地址回一张不同的图（默认都是同一张）
        self.pages = {
            "shop.example": "<html><head><title>山间民宿</title></head><body>"
            '<article><p>两间卧室</p><img src="/room.jpg"></article></body></html>'
        }

    def __call__(self, request):
        url = str(request.url)
        host = request.url.host
        self.requests.append(url)
        if host == "www.jianpian.cn":
            return html_response(article_html(self.state))
        if host in ("v.xiumius.cn", "s.leleku.cn"):
            location = XIUMI_PAGE if host == "v.xiumius.cn" else ZIWOYOU_ROUTE
            return httpx.Response(302, headers={"location": location})
        if host in ("d.xiumius.cn", "h5.brand.example"):
            return html_response(xiumi_page())
        if host == "sd.xiumius.cn":
            return json_response(XIUMI_SHOW, compress=True)
        if host == "gym.navo.top":
            return html_response(editor135_page())
        if host in self.pages:
            return html_response(self.pages[host])
        if host == "task-api.zowoyoo.com":
            return self.ziwoyou(request)
        if host in (
            "img-volc.jianpian.info",
            "img.xiumi.us",
            "qnimg.zowoyoo.com",
            "bdn.135editor.com",
        ):
            self.downloads.append(url)
            image = png(size=(40 + len(self.downloads), 30)) if self.distinct else self.image
            return httpx.Response(
                404 if url in self.broken else 200,
                content=image,
                headers={"content-type": "image/png"},
            )
        raise AssertionError(f"unexpected request: {url}")

    def ziwoyou(self, request):
        path = request.url.path
        if path == "/api/user/saasInfo":
            assert request.url.params["url"] == "amNqcS56aXdveW91Lm5ldA=="  # jcjq.ziwoyou.net
            return json_response(
                {"state": 1, "data": {"parentCustId": "3321486", "webName": "菊城假期"}}
            )
        if path == "/api/user/login":
            assert request.method == "POST" and request.headers["channel"] == "19"
            self.logins.append(json.loads(request.content))
            return json_response(
                {"state": 1, "data": {"loginState": 0}}, {"authtoken": "Bearer guest-token"}
            )
        if request.headers.get("authtoken") != "Bearer guest-token":
            return httpx.Response(401)
        data = {
            "/api/product/common/prodDetail/50946091": ZIWOYOU_DETAIL,
            "/api/product/common/product/content/50946091": ZIWOYOU_CONTENT,
            "/api/product/common/product/desc/50946091": "<p>抢购流程：预约后到酒店入住</p>",
        }[path]
        return json_response({"state": 1, "msg": "ok", "data": data})


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def source_server(monkeypatch):
    monkeypatch.setattr(
        webpage, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))]
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
    first = parse_article(article_html(), URL).images[0].url
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


def test_reply_from_a_source_chat_makes_a_draft_with_the_sources_images(client, source_server):
    source_server.distinct = True
    source_server.broken.add(parse_article(article_html(), URL).images[1].url)
    source = imported(client)
    downloaded = [img["sha"] for img in source["images"] if img["sha"]]
    assert len(downloaded) == 47
    chat = client.post("/api/agent/chats", json={"source_id": source["source_id"]}).json()

    found = client.get(f"/api/agent/chats/{chat['id']}/images").json()
    assert [img["sha"] for img in found["images"]] == downloaded  # 没下载下来的那张不在里面
    assert {(img["origin"], img["kind"]) for img in found["images"]} == {("source", "正文图")}
    assert found["selected"] == downloaded[:9]

    text = "民宿" * 1000
    path = f"/api/accounts/{UNB}/agent/chats/{chat['id']}/draft"
    draft = client.post(path, json={"description": text}).json()
    assert draft["description"] == text and len(text) > MAX_DESC  # 不截断
    assert [img["sha"] for img in draft["images"]] == downloaded[:9]
    assert draft["pdd_goods_id"] is None
    picked = downloaded[-2:]
    again = client.post(path, json={"description": text, "images": picked}).json()
    assert [img["sha"] for img in again["images"]] == picked

    # 货源删掉以后对话里没有能带的图了，已经建好的草稿里的图还在
    client.delete(f"/api/sources/{source['source_id']}")
    found = client.get(f"/api/agent/chats/{chat['id']}/images").json()
    assert found == {"images": [], "selected": []}
    image = client.get(f"/api/accounts/{UNB}/drafts/{draft['id']}/images/{downloaded[0]}")
    assert image.status_code == 200


def test_shared_source_image_is_kept_until_last_source_is_removed(client, tmp_path):
    first = imported(client)
    second = client.post("/api/sources", json={"url": URL + "?version=2"}).json()["source"]
    assert first["source_id"] != second["source_id"]
    path = tmp_path / "images" / f"{first['cover_sha']}.jpg"
    client.delete(f"/api/sources/{first['source_id']}")
    assert path.exists()
    client.delete(f"/api/sources/{second['source_id']}")
    assert not path.exists()


@pytest.mark.parametrize(
    ("url", "parser", "site"),
    [
        (URL, "jianpian", "简篇"),
        ("https://www.meipian.cn/abc", "jianpian", "美篇"),
        (XIUMI_SHARE, "xiumi", "秀米"),
        (ZIWOYOU_ROUTE, "ziwoyou", "自我游分销商城"),
        (
            "http://jcjq.ziwoyou.net/newb2c/productdetail/50946091?userId=a",
            "ziwoyou",
            "自我游分销商城",
        ),
        (SPVIEW, "editor135", "135编辑器"),
    ],
)
def test_url_patterns_pick_the_scheme(url, parser, site):
    scheme, (matched_site, _) = match_url(url)
    assert (scheme.info.id, matched_site) == (parser, site)


def test_no_scheme_for_unknown_or_bare_links():
    assert match_url("https://shop.example/a") is None
    assert match_url("https://xiumi.us/") is None
    assert match_url("http://jcjq.ziwoyou.net/m2/") is None


def test_ziwoyou_product_id_and_params_from_path_or_hash_route():
    assert ziwoyou_product(ZIWOYOU_ROUTE) == (
        "50946091",
        {
            "treeId": "12",
            "infoId": "50946091",
            "navigationKey": "2bcd6c56",
            "userId": "zxzad",
            "orderCustId": "6813241",
        },
    )
    assert ziwoyou_product("http://a.ziwoyou.net/m2/?userId=u#/productdetail/7") == (
        "7",
        {"userId": "u"},
    )
    assert ziwoyou_product("http://a.ziwoyou.net/newb2c/productdetail/9?x=1") == ("9", {"x": "1"})


def detect(client, url):
    response = client.post("/api/sources/detect", json={"url": url})
    assert response.status_code == 200, response.text
    return response.json()


def test_parsers_are_listed_for_manual_choice(client):
    parsers = client.get("/api/sources/parsers").json()["parsers"]
    assert [p["id"] for p in parsers] == ["jianpian", "xiumi", "ziwoyou", "editor135", "generic"]
    assert all(p["name"] and p["sites"] and p["summary"] for p in parsers)


def test_detect_by_url_short_link_and_page_features(client, source_server):
    xiumi = detect(client, XIUMI_SHARE)
    assert xiumi["parser"] == "xiumi" and xiumi["site"] == "秀米"
    assert xiumi["matched_by"] == "url" and xiumi["reason"] == "按网址识别"
    assert source_server.requests == []  # 认得网址就不请求

    short = detect(client, LELEKU)
    assert short["parser"] == "ziwoyou" and short["matched_by"] == "redirect"
    assert short["url"] == ZIWOYOU_ROUTE and short["host"] == "jcjq.ziwoyou.net"
    assert short["reason"] == "短链接 s.leleku.cn 跳转到 jcjq.ziwoyou.net，按跳转后的网址识别"
    assert "商品号 50946091" in short["link"]
    assert source_server.requests == [LELEKU]  # 跳到认识的商品页就停，不打开单页应用

    assert detect(client, SPVIEW)["parser"] == "editor135"

    custom = detect(client, "https://h5.brand.example/board/1")
    assert (custom["parser"], custom["matched_by"]) == ("xiumi", "page")
    assert custom["link"] == "秀米图文作品（自定义域名）"

    plain = detect(client, "https://shop.example/stay")
    assert (plain["parser"], plain["matched_by"], plain["site"]) == ("generic", "fallback", "网页")


def test_import_xiumi_reads_show_data_and_original_images(client, source_server):
    response = client.post("/api/sources", json={"url": XIUMI_SHARE})
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source["parser"] == "xiumi" and source["parser_name"] == "秀米作品数据"
    assert source["platform"] == "秀米" and source["author"] == "文案"
    assert source["url"] == XIUMI_SHARE and source["host"] == "v.xiumius.cn"
    assert source["title"] == "【双十一】温泉度假村"
    assert source["content"] == "何以解忧，唯有温泉！\n龙门十三汤\n森林氧吧泡池房"
    assert [img["url"] for img in source["images"]] == [
        "https://img.xiumi.us/xmi/ua/a.jpg",
        "https://img.xiumi.us/xmi/ua/b.png",
    ]
    assert source["missing_images"] == 0
    assert "背景层" not in source["content"]


def test_import_ziwoyou_short_link_logs_in_as_guest(client, source_server):
    response = client.post("/api/sources", json={"url": LELEKU})
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source["parser"] == "ziwoyou" and source["platform"] == "自我游分销商城"
    assert source["author"] == "菊城假期"
    assert source["url"] == (
        "http://jcjq.ziwoyou.net/newb2c/productdetail/50946091?userId=zxzad&orderCustId=6813241"
    )
    assert source["title"] == ZIWOYOU_DETAIL["infoTitle"]
    content = source["content"]
    assert "售价：1280 元/套（门市价 1499 元）" in content and "1030" not in content
    assert "景点/酒店：翔顺金水台莲花温泉（广东省云浮市新兴县）" in content
    assert "【图文详情】\n寻一处温泉，置身其中" in content
    assert "【退改说明】\n购买后不设退款" in content
    assert content.endswith("【预订须知】\n抢购流程：预约后到酒店入住")
    assert [img["url"] for img in source["images"]] == [
        "https://qnimg.zowoyoo.com/img/2529346/main.jpg",
        "https://qnimg.zowoyoo.com/img/prod/2529346/1.jpeg",
    ]
    (login,) = source_server.logins
    assert login["parentCustId"] == "3321486" and login["userId"] == "zxzad"
    assert login["orderCustId"] == "6813241" and len(login["openId"]) == 45
    assert not any("jcjq.ziwoyou.net" in url for url in source_server.requests)


def test_import_editor135_custom_domain_reads_script_data(client, source_server):
    source = client.post("/api/sources", json={"url": SPVIEW}).json()["source"]
    assert source["parser"] == "editor135" and source["platform"] == "135编辑器"
    assert source["title"] == "【简介】新兴翔顺象窝酒店"
    assert source["content"] == "离开城市的钢筋水泥\n与土地、树木近距离接触\n象窝山生态园"
    # 只取文章里的图，页面框架上的标志不要；图床升级 https
    assert [img["url"] for img in source["images"]] == [
        "https://qnimg.zowoyoo.com/img/prod/2529346/2.gif"
    ]


def test_manual_parser_choice_is_used_and_mismatch_is_reported(client, source_server):
    wrong = client.post("/api/sources", json={"url": SPVIEW, "parser": "xiumi"})
    assert wrong.status_code == 400
    assert "网页里没有秀米作品信息；可以换一个解析方案再试" in wrong.json()["message"]
    unknown = client.post("/api/sources", json={"url": SPVIEW, "parser": "nope"})
    assert unknown.status_code == 400 and "没有这个解析方案" in unknown.json()["message"]

    generic = client.post("/api/sources", json={"url": SPVIEW, "parser": "generic"}).json()
    assert generic["source"]["parser"] == "generic"
    assert generic["source"]["parser_name"] == "通用网页正文"
    again = client.post("/api/sources", json={"url": SPVIEW, "parser": "auto"}).json()
    assert again["added"] is False and again["source"]["parser"] == "editor135"
    listing = client.get("/api/sources").json()["sources"]
    assert [(s["parser"], s["host"]) for s in listing] == [("editor135", "gym.navo.top")]


def test_generic_fallback_reuses_the_page_fetched_while_detecting(client, source_server):
    source = client.post("/api/sources", json={"url": "https://shop.example/stay"}).json()["source"]
    assert source["parser"] == "generic" and source["content"] == "两间卧室"
    assert source_server.requests.count("https://shop.example/stay") == 1
