import asyncio
import io

import pytest
from fakes import POI_HOME, UNB, png
from PIL import Image

from app.drafts import DraftNotFoundError, DraftService
from app.images import ImageStore, normalize
from app.items import ItemService
from xianyu_core.publish import PublishBlockedError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    return ImageStore(tmp_path / "images")


@pytest.fixture
def items(accounts, database):
    return ItemService(database, accounts, interval=0)


@pytest.fixture
def service(accounts, database, items, store):
    return DraftService(database, accounts, items, store)


def fill(service, draft, **overrides):
    """把草稿填到能发布：描述、售价、发货地。"""
    fields = {
        "description": "宜家落地灯，九成新",
        "price_cent": 4900,
        "orig_price_cent": None,
        "post_mode": "free",
        "post_price_cent": None,
        "take_self": False,
        "image_order": [img.sha for img in service.get(UNB, draft.id).images],
        "address": {
            "area": "光明区",
            "city": "深圳",
            "divisionId": 440311,
            "gps": "22.7,113.9",
            "poiId": "B0F",
            "poiName": "光谷苑",
            "prov": "广东",
            "extra": "丢掉",
        },
    }
    return service.update(UNB, draft.id, **(fields | overrides))


def ready(service, images=1):
    draft = service.create(UNB)
    for i in range(images):
        service.add_image(UNB, draft.id, png(color=(i * 20, 0, 0)))
    fill(service, draft)
    return run(service.recommend(UNB, draft.id))


# ---- 图片 ----


def test_normalize_converts_to_jpeg_and_flattens_alpha():
    content, width, height = normalize(png(color=(0, 0, 0, 0), mode="RGBA"))
    with Image.open(io.BytesIO(content)) as img:
        assert (img.format, img.mode, img.size) == ("JPEG", "RGB", (80, 60))
        assert img.getpixel((0, 0)) == (255, 255, 255)  # 透明铺白
    assert (width, height) == (80, 60)


def test_normalize_shrinks_large_and_rotates_by_exif():
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # 拍的时候横着拿，要顺时针转 90 度
    Image.new("RGB", (4000, 1000)).save(out, "JPEG", exif=exif)
    _, width, height = normalize(out.getvalue())
    assert (width, height) == (512, 2048)


@pytest.mark.parametrize("data", [b"not an image", b""])
def test_normalize_rejects_non_images(data):
    with pytest.raises(ValueError, match="不是能识别的图片"):
        normalize(data)


def test_store_dedups_by_content(store):
    a = store.save(png())
    assert store.save(png()) == a
    assert store.read(a.sha)[:2] == b"\xff\xd8"
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")


# ---- 本地草稿 ----


def test_create_update_and_list(service):
    draft = service.create(UNB)
    assert (draft.description, draft.post_mode, draft.address) == ("", "free", None)
    service.add_image(UNB, draft.id, png("red"))
    updated = fill(service, draft, post_mode="fixed", post_price_cent=800)
    assert updated.price_cent == 4900
    assert updated.post_price_cent == 800
    assert "extra" not in updated.address
    # 不是一口价时邮费不存
    assert fill(service, draft, post_price_cent=800).post_price_cent is None
    # 新草稿沿用上一个选过的发货地
    assert service.create(UNB).address["poiId"] == "B0F"
    assert [d.id for d in service.list(UNB)][1] == draft.id


def test_images_add_reorder_remove(service, store):
    draft = service.create(UNB)
    service.add_image(UNB, draft.id, png("red"))
    draft = service.add_image(UNB, draft.id, png("blue"))
    red, blue = (img.sha for img in draft.images)
    with pytest.raises(ValueError, match="已经加过了"):
        service.add_image(UNB, draft.id, png("red"))
    assert [i.sha for i in fill(service, draft, image_order=[blue, red]).images] == [blue, red]
    with pytest.raises(ValueError, match="对不上"):
        fill(service, draft, image_order=[blue])

    other = service.create(UNB)
    service.add_image(UNB, other.id, png("red"))
    service.remove_image(UNB, draft.id, red)
    assert store.path(red).exists()  # 别的草稿还在用
    service.remove_image(UNB, draft.id, blue)
    assert not store.path(blue).exists()
    with pytest.raises(DraftNotFoundError):
        service.image(UNB, draft.id, red)
    service.delete(UNB, other.id)
    assert not store.path(red).exists()


def test_at_most_nine_images(service):
    draft = service.create(UNB)
    for i in range(9):
        service.add_image(UNB, draft.id, png(color=(i * 25, 0, 0)))
    with pytest.raises(ValueError, match="最多 9 张"):
        service.add_image(UNB, draft.id, png("white"))


def test_other_accounts_drafts_are_not_found(service):
    draft = service.create(UNB)
    with pytest.raises(DraftNotFoundError):
        service.get("999", draft.id)


# ---- 推荐分类 ----


def test_recommend_then_choose_keeps_selection(service, platform):
    draft = service.create(UNB)
    with pytest.raises(ValueError, match="先写描述"):
        run(service.recommend(UNB, draft.id))
    fill(service, draft)
    draft = run(service.recommend(UNB, draft.id))
    assert draft.category["catName"] == "落地灯"
    brand = next(p for p in draft.properties() if p["id"] == "20000")
    assert (brand["name"], brand["selected"]) == ("品牌", ["6277054"])

    draft = run(service.choose(UNB, draft.id, "-10000", ["126858404"]))
    assert draft.category["catName"] == "台灯"
    api, data = platform.calls[-1]
    assert api == "mtop.taobao.idle.kgraph.property.recommend"
    assert (data["catId"], data["catName"]) == ("50025404", "台灯")
    assert data["selectedList"][0]["properties"] == "-10000##分类:126858404##台灯"
    # 再推荐一次（比如改了描述），之前的选择带着
    assert run(service.recommend(UNB, draft.id)).category["catName"] == "台灯"


def test_addresses_put_last_used_first(service):
    addresses = run(service.addresses(UNB))
    assert [a["name"] for a in addresses] == ["光谷苑", "万达广场"]
    assert addresses[0]["address"]["gps"] == f"{POI_HOME['latitude']},{POI_HOME['longitude']}"
    assert addresses[0]["detail"] == "广东深圳光明区同业路340号"


# ---- 发布 ----


def test_publish_uploads_images_and_records_item(service, platform, items):
    draft = ready(service, images=2)
    draft = run(service.publish(UNB, draft.id))

    assert draft.item_id == "9001"
    assert draft.published_at and draft.error is None
    assert len(platform.uploads) == 2
    [body] = platform.published
    assert [img["url"] for img in body["imageInfoDOList"]] == [
        "https://img.example/up1.jpg",
        "https://img.example/up2.jpg",
    ]
    assert body["userRightsProtocols"] == [
        {"enable": True, "serviceCode": "SVC"},
        {"enable": False, "serviceCode": "AI_SALE"},
    ]
    assert body["sourceId"] == "pcMainPublish"
    apis = [api for api, _ in platform.calls]
    assert apis[-4:] == [
        "mtop.idle.pc.idleitem.prepublish.check",
        "mtop.taobao.idleitem.badwords.prepubcheck",
        "mtop.idle.item.publish.service.cards.list",
        "mtop.idle.pc.idleitem.publish",
    ]
    item = items.get(UNB, "9001")
    assert (item.title, item.price_cent, item.pic_url) == (
        "宜家落地灯，九成新",
        4900,
        "https://img.example/up1.jpg",
    )
    [op] = items.history(UNB, "9001")
    assert (op.action, op.ok, op.detail) == ("publish", True, {"draft_id": draft.id, "price": 4900})
    with pytest.raises(ValueError, match="已经发布了"):
        run(service.publish(UNB, draft.id))


def test_failed_publish_keeps_uploads_for_retry(service, platform, items):
    draft = ready(service, images=2)
    platform.fail["mtop.idle.pc.idleitem.publish"] = "FAIL_BIZ_X::类目不对"
    with pytest.raises(Exception, match="类目不对"):
        run(service.publish(UNB, draft.id))
    draft = service.get(UNB, draft.id)
    assert "类目不对" in draft.error
    assert all(img.url for img in draft.images)
    [op] = items.history(UNB)
    assert (op.action, op.ok, op.item_id) == ("publish", False, "")

    del platform.fail["mtop.idle.pc.idleitem.publish"]
    assert run(service.publish(UNB, draft.id)).item_id == "9001"
    assert len(platform.uploads) == 2  # 重试时没再传


def test_local_checks_happen_before_any_request(service, platform):
    draft = service.create(UNB)
    service.add_image(UNB, draft.id, png())
    fill(service, draft)
    with pytest.raises(ValueError, match="先推荐分类"):
        run(service.publish(UNB, draft.id))
    fill(service, draft, address=None)
    with pytest.raises(ValueError, match="发货地"):
        run(service.publish(UNB, draft.id))
    fill(service, draft, description="充电宝 10000 毫安")
    run(service.recommend(UNB, draft.id))
    with pytest.raises(ValueError, match="3C证书编号"):
        run(service.publish(UNB, draft.id))
    calls = len(platform.calls)
    assert service.get(UNB, draft.id).error is None
    assert calls == len(platform.calls)


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda p: setattr(p, "publish_limited", True), "发布受限"),
        (lambda p: p.bad_words.append("代购"), "含违禁词：代购"),
    ],
)
def test_platform_blocks_before_uploading(service, platform, setup, message):
    draft = ready(service)
    setup(platform)
    with pytest.raises(PublishBlockedError, match=message):
        run(service.publish(UNB, draft.id))
    assert platform.uploads == []
    assert message.split("：")[0] in service.get(UNB, draft.id).error
