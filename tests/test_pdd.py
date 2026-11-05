import copy
import json
from pathlib import Path

import httpx
import pytest
from fakes import PASSWORD, UNB, add_user, png, sign_in
from fastapi.testclient import TestClient

from app.api import create_app
from app.pdd import draft_text, media_url, parse
from xianyu_core.guard import Guard
from xianyu_core.publish import MAX_DESC

# 2026-11 在一个真实商品页上用插件的 readPddGoods 读出来的（主图、详情图、规格各留了 3 个）
PAYLOAD = json.loads((Path(__file__).parent / "pdd_goods.json").read_text())
GOODS_ID = "909497970646"
FIRST_IMAGE = (
    "https://img.pddpic.com/mms-goods-image/2026-08-05/02cf72b4-74b9-48cb-93fb-b84e9d24d256.jpeg"
)


def payload():
    return copy.deepcopy(PAYLOAD)


def test_parse_real_page():
    goods = parse(payload())
    assert goods.goods_id == GOODS_ID
    assert goods.title.startswith("中国扬子集团洗衣机")
    assert goods.mall_name == "YANGZI咖麦菲专卖店"
    assert goods.sales_tip == "热卖12.4万+件"
    assert (goods.min_price_cent, goods.max_price_cent) == (29800, 66800)
    assert (goods.min_normal_price_cent, goods.max_normal_price_cent) == (49800, 86800)
    # 主图去掉 ?imageMogr2 缩略参数就是原图
    assert [img.url for img in goods.images][0] == FIRST_IMAGE
    assert all("?" not in img.url for img in goods.images)
    assert len(goods.images) == 3
    # 详情图带页面上的宽高
    assert [(img.width, img.height) for img in goods.detail_images][1] == (780, 1200)
    # 主图视频、详情视频；详情视频带封面
    assert [(v.url.rsplit("/", 1)[1], bool(v.cover)) for v in goods.videos] == [
        ("a9da07b821d1afba37515386afeda574.mp4", False),
        ("a9da07b821d1afba37515386afeda574.mp4.f30.mp4", True),
    ]
    sku = goods.skus[0]
    assert sku.sku_id == "1869535634868"
    assert sku.specs == [{"key": "款式", "value": "10KG升级强劲新蓝光洗护【强动力桶自洁+加厚款】"}]
    assert (sku.price_cent, sku.normal_price_cent, sku.quantity) == (29800, 49800, 1000)
    assert sku.on_sale and sku.thumb.url.endswith("048bcec2-c91a-4575-b225-5ec640a0daf5.jpeg")
    assert goods.properties[:2] == [
        {"key": "品牌", "value": "YANGZI/扬子"},
        {"key": "型号", "value": "XQB100-8588"},
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("//img.pddpic.com/a.jpg", "https://img.pddpic.com/a.jpg"),
        ("http://img.pddpic.com/a.jpg?imageView2/2/w/300", "https://img.pddpic.com/a.jpg"),
        ("https://video5.pddpic.com/i3/v.mp4", "https://video5.pddpic.com/i3/v.mp4"),
        ("https://evil.example/a.jpg", None),
        ("https://pddpic.com.evil.example/a.jpg", None),
        ("file:///etc/passwd", None),
        ("", None),
        (42, None),
    ],
)
def test_only_pdd_media_urls(url, expected):
    assert media_url(url) == expected


def test_parse_skips_foreign_and_broken_entries():
    data = payload()
    data["goods"]["topGallery"] = [
        {"url": "https://evil.example/x.jpg"},
        "not a dict",
        {"url": FIRST_IMAGE},
        {"url": FIRST_IMAGE + "?imageMogr2/thumbnail/300x"},  # 同一张图
    ]
    data["goods"]["minGroupPrice"] = "abc"
    data["goods"]["skus"] = [{"skuId": 1, "specs": "x", "groupPrice": None}]
    goods = parse(data)
    assert [img.url for img in goods.images] == [FIRST_IMAGE]
    assert goods.min_price_cent is None
    assert goods.skus[0].specs == [] and goods.skus[0].price_cent is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("goods"), "没有商品数据"),
        (lambda d: d["goods"].pop("goodsID"), "没有商品 ID"),
        (lambda d: d["goods"].update(goodsName=" "), "没有标题"),
    ],
)
def test_parse_needs_id_and_title(change, message):
    data = payload()
    change(data)
    with pytest.raises(ValueError, match=message):
        parse(data)


def test_draft_text():
    text = draft_text(parse(payload()))
    lines = text.splitlines()
    assert lines[0].startswith("中国扬子集团洗衣机")
    assert "品牌：YANGZI/扬子" in lines
    assert lines.index("可选款式：") > lines.index("排水方式：下排式")
    assert "- 12KG升级强劲新蓝光洗护【强动力桶自洁+加厚玻璃款】" in lines


def test_draft_text_fits_xianyu_limit():
    data = payload()
    sku = data["goods"]["skus"][0]
    data["goods"]["skus"] = [
        {**sku, "skuId": i, "specs": [{"spec_key": "款式", "spec_value": f"款式{i}" * 10}]}
        for i in range(100)
    ]
    text = draft_text(parse(data))
    assert len(text) <= MAX_DESC
    assert text.startswith("中国扬子集团洗衣机") and "品牌：YANGZI/扬子" in text


class FakeCdn:
    """假的拼多多 CDN：每个地址回一张不同颜色的图，`broken` 里的地址回 404。"""

    def __init__(self):
        self.broken: set[str] = set()
        self.requests: list[str] = []
        self._colors: dict[str, tuple[int, int, int]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.broken:
            return httpx.Response(404)
        color = self._colors.setdefault(url, (len(self._colors) * 7 % 256, 80, 160))
        return httpx.Response(200, content=png(color=color, size=(120, 120)))


@pytest.fixture
def cdn():
    return FakeCdn()


@pytest.fixture
def client(tmp_path, accounts, platform, cdn, database_url):
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("pddpic.com"):
            return cdn(request)
        return platform(request)

    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(route),
        interval=0,
        web_dist=None,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        yield client


@pytest.fixture
def plugin(client):
    token = client.post("/api/auth/plugin-token").json()["token"]
    return {"authorization": f"Bearer {token}"}


def image_files(tmp_path):
    return sorted(p.name for p in (tmp_path / "images").glob("*.jpg"))


def test_import_with_plugin_token_downloads_images(client, plugin, cdn, tmp_path):
    resp = client.post("/api/pdd/goods", json=payload(), headers=plugin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 主图 3 + 详情图 3（第一张和主图第一张是同一个地址）+ 规格图 3
    assert body["added"] is True
    assert (body["saved"], body["total"]) == (8, 8)
    assert len(cdn.requests) == 8
    assert len(image_files(tmp_path)) == 8
    goods = body["goods"]
    assert goods["goods_id"] == GOODS_ID
    assert goods["url"] == f"https://mobile.yangkeduo.com/goods.html?goods_id={GOODS_ID}"
    assert goods["cover"] == FIRST_IMAGE
    assert all(img["saved"] for img in goods["images"] + goods["detail_images"])
    assert goods["skus"][0]["name"] == "10KG升级强劲新蓝光洗护【强动力桶自洁+加厚款】"
    assert goods["missing_images"] == 0

    listed = client.get("/api/pdd/goods").json()["goods"]
    assert [(g["goods_id"], g["sku_count"], g["image_count"]) for g in listed] == [(GOODS_ID, 3, 3)]
    assert "skus" not in listed[0]
    assert client.get(f"/api/pdd/goods/{GOODS_ID}").json()["properties"][0]["key"] == "品牌"


def test_plugin_token_cannot_read_goods(client, plugin):
    client.post("/api/pdd/goods", json=payload(), headers=plugin)
    assert client.get("/api/pdd/goods", headers=plugin).status_code == 401
    assert client.get(f"/api/pdd/goods/{GOODS_ID}", headers=plugin).status_code == 401


def test_import_needs_a_token(client):
    resp = client.post(
        "/api/pdd/goods", json=payload(), headers={"authorization": "Bearer xymp_bad"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthenticated"


def test_bad_payload_is_400(client, plugin):
    data = payload()
    data["goods"].pop("goodsName")
    resp = client.post("/api/pdd/goods", json=data, headers=plugin)
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"


def test_failed_downloads_can_be_retried(client, plugin, cdn):
    cdn.broken.add(FIRST_IMAGE)
    body = client.post("/api/pdd/goods", json=payload(), headers=plugin).json()
    assert (body["saved"], body["total"]) == (7, 8)
    assert body["goods"]["missing_images"] == 1
    assert body["goods"]["images"][0]["saved"] is False

    cdn.broken.clear()
    cdn.requests.clear()
    body = client.post(f"/api/pdd/goods/{GOODS_ID}/images").json()
    assert cdn.requests == [FIRST_IMAGE]  # 只下没下来的那张
    assert (body["saved"], body["total"]) == (8, 8)


def test_reimport_updates_and_keeps_downloaded_images(client, plugin, cdn, tmp_path):
    client.post("/api/pdd/goods", json=payload(), headers=plugin)
    first = client.get(f"/api/pdd/goods/{GOODS_ID}").json()
    files = image_files(tmp_path)
    cdn.requests.clear()

    data = payload()
    data["goods"]["goodsName"] = "改了标题"
    dropped = data["goods"]["topGallery"].pop()  # 商品页上少了一张主图
    data["goods"]["topGallery"].append({"url": "https://img.pddpic.com/new.jpeg"})
    body = client.post("/api/pdd/goods", json=data, headers=plugin).json()
    assert body["added"] is False
    assert cdn.requests == ["https://img.pddpic.com/new.jpeg"]
    goods = client.get(f"/api/pdd/goods/{GOODS_ID}").json()
    assert goods["title"] == "改了标题"
    assert goods["imported_at"] == first["imported_at"]
    assert goods["updated_at"] >= first["updated_at"]
    assert media_url(dropped["url"]) not in [img["url"] for img in goods["images"]]
    # 少了的那张删掉，新的那张加上
    assert len(image_files(tmp_path)) == len(files)
    assert image_files(tmp_path) != files
    assert len(client.get("/api/pdd/goods").json()["goods"]) == 1


def test_draft_from_goods(client, plugin, tmp_path):
    client.post("/api/pdd/goods", json=payload(), headers=plugin)
    resp = client.post(f"/api/accounts/{UNB}/pdd/{GOODS_ID}/draft")
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["pdd_goods_id"] == GOODS_ID
    assert draft["price_cent"] is None
    assert draft["description"].startswith("中国扬子集团洗衣机")
    assert len(draft["images"]) == 3
    assert draft["images"][0]["width"] == 120
    # 草稿里的图能取，在发布页列表里
    sha = draft["images"][0]["sha"]
    assert client.get(f"/api/accounts/{UNB}/drafts/{draft['id']}/images/{sha}").status_code == 200
    assert [d["id"] for d in client.get(f"/api/accounts/{UNB}/drafts").json()["drafts"]] == [
        draft["id"]
    ]

    # 删货源：草稿还在用的图留着，别的删掉
    assert client.delete(f"/api/pdd/goods/{GOODS_ID}").json() == {"ok": True}
    assert client.get("/api/pdd/goods").json()["goods"] == []
    assert len(image_files(tmp_path)) == 3
    assert client.get(f"/api/accounts/{UNB}/drafts/{draft['id']}/images/{sha}").status_code == 200

    # 再删草稿，图都没人用了
    client.delete(f"/api/accounts/{UNB}/drafts/{draft['id']}")
    assert image_files(tmp_path) == []


def test_draft_needs_downloaded_images(client, plugin, cdn):
    cdn.broken.update(media_url(img["url"]) for img in PAYLOAD["goods"]["topGallery"])
    client.post("/api/pdd/goods", json=payload(), headers=plugin)
    resp = client.post(f"/api/accounts/{UNB}/pdd/{GOODS_ID}/draft")
    assert resp.status_code == 400
    assert "重新下载图片" in resp.json()["message"]


def test_goods_belong_to_the_importing_user(client, plugin, database_url):
    client.post("/api/pdd/goods", json=payload(), headers=plugin)
    add_user(database_url, username="other")
    resp = client.post("/api/auth/login", json={"username": "other", "password": PASSWORD})
    other = {"authorization": f"Bearer {resp.json()['access_token']}"}

    assert client.get("/api/pdd/goods", headers=other).json()["goods"] == []
    assert client.get(f"/api/pdd/goods/{GOODS_ID}", headers=other).status_code == 404
    assert client.delete(f"/api/pdd/goods/{GOODS_ID}", headers=other).status_code == 404
    # 别人名下的账号建不了草稿
    assert (
        client.post(f"/api/accounts/{UNB}/pdd/{GOODS_ID}/draft", headers=other).status_code == 404
    )
    assert len(client.get("/api/pdd/goods").json()["goods"]) == 1


def test_missing_goods_is_404(client):
    resp = client.get("/api/pdd/goods/123")
    assert resp.status_code == 404
    assert resp.json()["code"] == "pdd_goods_not_found"
    assert client.post(f"/api/accounts/{UNB}/pdd/123/draft").status_code == 404
