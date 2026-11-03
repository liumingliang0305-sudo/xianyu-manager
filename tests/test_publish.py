import json

import pytest
from fakes import recommend_result

from xianyu_core.publish import (
    UploadedImage,
    address_from_poi,
    build_publish_payload,
    category_from,
    check_desc,
    choose,
    labels_from_cards,
    submit_payload,
    unsupported_cards,
)

ADDRESS = {"city": "深圳", "divisionId": 440311, "gps": "22.7,113.9", "poiId": "B0F"}
IMAGES = [UploadedImage("https://img.example/a.jpg", 800, 600), UploadedImage("b", 10, 20)]


def cards(desc="宜家落地灯"):
    return recommend_result({"description": desc})["cardList"]


def payload(**overrides):
    kwargs = {
        "desc": "宜家落地灯，九成新",
        "images": IMAGES,
        "cards": cards(),
        "category": category_from({"cardList": cards()}),
        "price_cent": 4900,
        "orig_price_cent": None,
        "post_mode": "free",
        "post_price_cent": None,
        "take_self": False,
        "address": ADDRESS,
    }
    return build_publish_payload(**(kwargs | overrides))


def test_labels_follow_web_format():
    labels = labels_from_cards(cards())
    assert [label["properties"] for label in labels] == [
        "-10000##分类:126858403##落地灯",
        "20000##品牌:6277054##IKEA/宜家",
    ]
    # transportData 原样带上，再加文字和是不是用户点的
    assert labels[1]["valueId"] == "6277054"
    assert (labels[1]["text"], labels[1]["isUserClick"]) == ("IKEA/宜家", "0")


def test_category_prefers_clicked_value_over_prediction():
    category = category_from({"cardList": cards(), "categoryPredictResult": {"catId": 1}})
    assert category["catId"] == "50025403"
    assert category["channelCatId"] == "126858403"
    unclicked = choose(cards(), "20000", ["11"])  # 分类没动，只是确认预测兜底
    for value in unclicked[0]["cardData"]["valuesList"]:
        value["isClicked"] = "0"
    predicted = {"catId": 7, "catName": "别的", "channelCatId": 8}
    assert category_from({"cardList": unclicked, "categoryPredictResult": predicted})["catId"] == 7


def test_choose_marks_user_click_and_cancel():
    changed = choose(cards(), "-10000", ["126858404"])
    values = changed[0]["cardData"]["valuesList"]
    assert [(v["isClicked"], v["isUserClick"], v["isUserCancel"]) for v in values] == [
        ("0", "0", "1"),
        ("1", "1", None),
    ]
    assert values[1]["transportData"]["properties"] == "-10000##分类:126858404##台灯"
    assert changed[1] is cards()[1] or changed[1] == cards()[1]  # 别的卡片不动


@pytest.mark.parametrize(
    ("property_id", "keys", "message"),
    [
        ("20000", ["nope"], "没有这个选项"),
        ("20000", ["11", "6277054"], "只能选一个"),
        ("9", ["1"], "没有这个属性"),
    ],
)
def test_choose_rejects_bad_selection(property_id, keys, message):
    with pytest.raises(ValueError, match=message):
        choose(cards(), property_id, keys)


def test_unsupported_cards():
    assert unsupported_cards(cards()) == []
    assert unsupported_cards(cards("充电宝")) == ["3C证书编号"]
    book = [{"cardType": "20401", "cardData": {"propertyName": "书名", "isBook": "true"}}]
    assert unsupported_cards(book) == ["书名"]


def test_address_from_poi_matches_web():
    poi = {
        "poi": "光谷苑",
        "poiId": "B0F",
        "area": "光明区",
        "city": "深圳",
        "prov": "广东",
        "divisionId": 440311,
        "latitude": "22.7",
        "longitude": "113.9",
    }
    assert address_from_poi(poi) == {
        "area": "光明区",
        "city": "深圳",
        "divisionId": 440311,
        "gps": "22.7,113.9",
        "poiId": "B0F",
        "poiName": "光谷苑",
        "prov": "广东",
    }
    assert address_from_poi({**poi, "poi": ""})["poiName"] == "深圳 光明区"


def test_payload_matches_web_publish_form():
    body = payload()
    assert body["itemTextDTO"] == {
        "desc": "宜家落地灯，九成新",
        "title": "宜家落地灯，九成新",
        "titleDescSeparate": False,
    }
    assert body["itemPriceDTO"] == {"priceInCent": "4900"}  # 没填原价不传
    assert body["itemPostFeeDTO"] == {
        "canFreeShipping": True,
        "supportFreight": True,
        "onlyTakeSelf": False,
    }
    first, second = body["imageInfoDOList"]
    assert first == {
        "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
        "isQrCode": False,
        "url": "https://img.example/a.jpg",
        "heightSize": 600,
        "widthSize": 800,
        "major": True,
        "type": 0,
        "status": "done",
    }
    assert second["major"] is False
    assert body["itemCatDTO"]["catName"] == "落地灯"
    assert body["itemAddrDTO"] == ADDRESS
    assert (body["quantity"], body["itemTypeStr"], body["defaultPrice"]) == ("1", "b", False)
    assert "uniqueCode" not in body
    submitted = submit_payload(body)
    assert submitted["sourceId"] == submitted["bizcode"] == submitted["publishScene"]
    assert submitted["uniqueCode"].isdigit()
    json.dumps(submitted)  # 能直接发


def test_post_modes():
    fixed = payload(post_mode="fixed", post_price_cent=800, take_self=True)["itemPostFeeDTO"]
    assert fixed == {
        "canFreeShipping": False,
        "supportFreight": True,
        "templateId": "0",
        "onlyTakeSelf": True,
        "postPriceInCent": "800",
    }
    assert payload(post_mode="distance")["itemPostFeeDTO"]["templateId"] == "-100"
    assert payload(post_mode="none")["itemPostFeeDTO"]["supportFreight"] is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"desc": "  "}, "描述不能为空"),
        ({"desc": "好用😀"}, "emoji"),
        ({"images": []}, "图片要 1 到 9 张"),
        ({"images": IMAGES * 5}, "图片要 1 到 9 张"),
        ({"price_cent": 0}, "售价要大于 0"),
        ({"post_mode": "fixed", "post_price_cent": None}, "一口价邮费"),
        ({"post_mode": "fixed", "post_price_cent": 100001}, "一口价邮费"),
        ({"post_mode": "air"}, "不认识的邮费方式"),
        ({"category": {}}, "先推荐分类"),
        ({"cards": cards("充电宝")}, "网页端不支持发布"),
    ],
)
def test_payload_rejects_what_the_web_form_rejects(overrides, message):
    with pytest.raises(ValueError, match=message):
        payload(**overrides)


def test_check_desc_length():
    check_desc("好" * 1500)
    with pytest.raises(ValueError, match="最多 1500"):
        check_desc("好" * 1501)
