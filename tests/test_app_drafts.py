import asyncio
import io

import pytest
from fakes import POI_HOME, UNB, png
from PIL import Image

from app.drafts import DraftNotFoundError, DraftService
from app.images import ImageStore, normalize
from app.items import ItemService, PriceGuardError
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


def edit_form(draft, **overrides):
    """编辑草稿现在的表单，`overrides` 是要改的。"""
    fields = {
        "description": draft.description,
        "price_cent": draft.price_cent,
        "orig_price_cent": draft.orig_price_cent,
        "post_mode": draft.post_mode,
        "post_price_cent": draft.post_price_cent,
        "take_self": draft.take_self,
        "image_order": [img.sha for img in draft.images],
        "address": draft.address,
    }
    return fields | overrides


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


# ---- 编辑已有商品 ----


def test_edit_item_loads_form_once(service, platform):
    draft = run(service.edit_item(UNB, "1001"))
    assert draft.edit_item_id == "1001" and draft.item_id is None
    assert (draft.description, draft.price_cent, draft.orig_price_cent) == ("洗衣机", 35800, None)
    assert (draft.post_mode, draft.post_price_cent, draft.take_self) == ("free", None, False)
    assert [(img.url, img.width, img.height) for img in draft.images] == [
        ("http://img.example/1001.jpg", 800, 600)
    ]
    assert draft.category["catName"] == "洗衣机"
    assert draft.address["poiName"] == "某广场"
    assert draft.current_labels() == [{"name": "成色", "value": "几乎全新"}]
    assert draft.can_keep_post() is False
    assert service.list(UNB) == []  # 不在发布页的列表里

    calls = len(platform.calls)
    assert run(service.edit_item(UNB, "1001")).id == draft.id  # 接着改，不再请求平台
    assert len(platform.calls) == calls
    assert [api for api, _ in platform.calls] == ["mtop.idle.pc.idleitem.editDetail"]


def test_submit_edit_changes_only_what_was_edited(service, platform, items):
    run(items.sync(UNB))
    draft = run(service.edit_item(UNB, "1001"))
    draft = service.add_image(UNB, draft.id, png("red"))
    old, new = (img.sha for img in draft.images)
    draft = service.update(
        UNB, draft.id, **edit_form(draft, description="洗衣机 九成新", image_order=[new, old])
    )
    draft = run(service.publish(UNB, draft.id))

    assert draft.item_id == "1001" and draft.error is None
    [body] = platform.edited
    assert body["itemTextDTO"]["desc"] == "洗衣机 九成新"
    first, second = body["imageInfoDOList"]
    assert (first["url"], first["major"]) == ("https://img.example/up1.jpg", True)
    # 原来的图照 editDetail 带回去，只是不再是首图
    assert (second["url"], second["major"], second["templateIndex"]) == (
        "http://img.example/1001.jpg",
        False,
        "27923_1",
    )
    # 没改的照原样：邮费带着 templateId，服务没重新查
    assert body["itemPostFeeDTO"]["templateId"] == "0"
    assert body["userRightsProtocols"] == [{"enable": True, "serviceCode": "OLD"}]
    assert body["itemPriceDTO"] == {"priceInCent": "35800"}
    assert [api for api, _ in platform.calls][-2:] == [
        "mtop.taobao.idleitem.badwords.prepubcheck",
        "mtop.idle.pc.idleitem.edit",
    ]
    [op] = items.history(UNB, "1001")
    assert (op.action, op.ok, op.detail) == (
        "edit",
        True,
        {"draft_id": draft.id, "fields": ["描述", "图片"]},
    )
    item = items.get(UNB, "1001")
    assert (item.title, item.pic_url) == ("洗衣机 九成新", "https://img.example/up1.jpg")
    with pytest.raises(ValueError, match="已经提交到闲鱼"):
        service.update(UNB, draft.id, **edit_form(draft))

    # 再编辑就按闲鱼上现在的样子重新建，提交过的删掉
    again = run(service.edit_item(UNB, "1001"))
    assert again.id != draft.id and again.description == "洗衣机 九成新"
    with pytest.raises(DraftNotFoundError):
        service.get(UNB, draft.id)


def test_submit_edit_checks_before_requesting(service, platform, items):
    draft = run(service.edit_item(UNB, "1001"))
    calls = len(platform.calls)
    with pytest.raises(ValueError, match="没有要提交的改动"):
        run(service.publish(UNB, draft.id))
    service.update(UNB, draft.id, **edit_form(draft, price_cent=358))  # 少输了两个 0
    with pytest.raises(PriceGuardError):
        run(service.publish(UNB, draft.id))
    assert len(platform.calls) == calls
    assert items.history(UNB) == []

    run(service.publish(UNB, draft.id, force=True))
    assert platform.items["1001"]["price"] == 358
    [op] = items.history(UNB)
    assert op.detail == {"draft_id": draft.id, "fields": ["价格"], "from": 35800, "to": 358}


def test_failed_edit_is_logged_and_can_retry(service, platform, items):
    draft = run(service.edit_item(UNB, "1001"))
    service.update(UNB, draft.id, **edit_form(draft, post_mode="fixed", post_price_cent=800))
    platform.fail["mtop.idle.pc.idleitem.edit"] = "FAIL_BIZ_ITEM_STATUS_ERROR::宝贝状态不允许编辑"
    with pytest.raises(Exception, match="不允许编辑"):
        run(service.publish(UNB, draft.id))
    assert "不允许编辑" in service.get(UNB, draft.id).error
    [op] = items.history(UNB, "1001")
    assert (op.action, op.ok, op.detail["fields"]) == ("edit", False, ["邮费"])

    del platform.fail["mtop.idle.pc.idleitem.edit"]
    run(service.publish(UNB, draft.id))
    post = platform.edited[-1]["itemPostFeeDTO"]
    assert post == {
        "canFreeShipping": False,
        "supportFreight": True,
        "templateId": "0",
        "onlyTakeSelf": False,
        "postPriceInCent": "800",
    }


def test_edit_keeps_post_template_from_app(service, platform):
    template = {"canFreeShipping": "false", "supportFreight": "true", "templateId": "778"}
    platform.items["1001"]["post"] = {**template, "onlyTakeSelf": "false"}
    draft = run(service.edit_item(UNB, "1001"))
    assert (draft.post_mode, draft.can_keep_post()) == ("keep", True)
    service.update(UNB, draft.id, **edit_form(draft, take_self=True))
    run(service.publish(UNB, draft.id))
    post = platform.edited[-1]["itemPostFeeDTO"]
    assert (post["templateId"], post["onlyTakeSelf"]) == ("778", True)

    other = service.create(UNB)  # 发新商品的草稿没有原来的邮费可以沿用
    with pytest.raises(ValueError, match="邮费方式"):
        fill(service, other, post_mode="keep")


def test_edit_recommend_starts_from_current_labels(service, platform):
    draft = run(service.edit_item(UNB, "1001"))
    draft = run(service.recommend(UNB, draft.id))
    _, data = platform.calls[-1]
    assert data["currentCardList"] == []
    assert [label["text"] for label in data["selectedList"]] == ["洗衣机", "几乎全新"]
    assert (data["catId"], data["catName"]) == ("50025413", "洗衣机")
    assert draft.current_labels() == [] and draft.properties()

    # 换了分类：属性按推荐的带，服务重新查
    draft = run(service.choose(UNB, draft.id, "-10000", ["126858404"]))
    run(service.publish(UNB, draft.id))
    body = platform.edited[-1]
    assert body["itemCatDTO"]["catName"] == "台灯"
    assert body["userRightsProtocols"][0]["serviceCode"] == "SVC"
    assert "mtop.idle.item.publish.service.cards.list" in [api for api, _ in platform.calls]


def test_multi_sku_items_cannot_be_edited(service, platform):
    platform.items["1001"]["sku"] = [{"priceInCent": "100"}]
    with pytest.raises(ValueError, match="多规格"):
        run(service.edit_item(UNB, "1001"))
