import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from xianyu_core.credential import Credential
from xianyu_core.mtop import MtopClient
from xianyu_core.search import (
    SEARCH_API,
    SearchApi,
    SearchCard,
    SearchQuery,
    build_payload,
)


def run(coro):
    return asyncio.run(coro)


def data_of(request: httpx.Request) -> dict:
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    return json.loads(form["data"])


def ok(data):
    return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": data})


def tag(content, kind="gradientImageText"):
    return {"data": {"type": kind, "content": content}}


def make_row(item_id, price="49.90", want="1434人想要", tag_arg="freeship", ori=None):
    """结构照 2026-11-02 实测的搜索结果裁剪，数值是编的。"""
    ex = {
        "itemId": item_id,
        "title": f"机械键盘{item_id}",
        "area": "广东",
        "userNickName": "外设小铺",
        "picUrl": f"http://img.example/{item_id}.heic",
        "price": [
            {"type": "sign", "text": "¥"},
            {"type": "integer", "text": "49"},
            {"type": "decimal", "text": ".90"},
        ],
        "fishTags": {
            "r1": {"tagList": [tag("freeShippingIcon", "img")]},
            "r2": {"tagList": [tag("几乎全新"), tag("HP/惠普")]},
            "r3": {"tagList": [tag("累计降价13%"), tag(want, "text")] if want else []},
            "r4": {"tagList": [tag("卖家信用极好")]},
            "r5": {"tagList": [tag("nfrIcon", "img")]},
        },
    }
    if ori:
        ex["oriPrice"] = ori
    args = {"price": price, "publishTime": "1774544225000", "tag": tag_arg, "wantNum": "0"}
    return {
        "data": {"item": {"main": {"exContent": ex, "clickParam": {"args": args}}}},
        "type": "DX",
    }


def result(rows, next_page=True, found=1500):
    return {
        "resultList": rows,
        "resultInfo": {
            "hasNextPage": next_page,
            "searchResControlFields": {"nextPage": next_page, "numFound": found},
        },
    }


def test_payload_without_filters_matches_web():
    """2026-11-02 对照网页端 p_search-index.js：综合排序、不筛选时的请求体。"""
    assert build_payload(SearchQuery(" 机械键盘 ")) == {
        "pageNumber": 1,
        "keyword": "机械键盘",
        "fromFilter": False,
        "rowsPerPage": 30,
        "sortValue": "",
        "sortField": "",
        "customDistance": "",
        "gps": "",
        "propValueStr": {},
        "customGps": "",
        "searchReqFromPage": "pcSearch",
        "extraFilterValue": "{}",
        "userPositionJson": "{}",
    }


def test_payload_with_sort_and_filters():
    query = SearchQuery(
        "键盘",
        sort="price_asc",
        publish_days=7,
        price_min_cent=3000,
        filters=("personal", "free_ship"),
    )
    payload = build_payload(query, page=2)
    assert payload["pageNumber"] == 2
    assert payload["fromFilter"] is True
    assert (payload["sortField"], payload["sortValue"]) == ("price", "asc")
    assert payload["propValueStr"] == {
        "searchFilter": (
            "publishDays:7;priceRange:30,undefined;quickFilter:filterPersonal,filterFreePostage;"
        )
    }


def test_payload_price_range_only_max():
    payload = build_payload(SearchQuery("键盘", price_max_cent=10050))
    assert payload["propValueStr"] == {"searchFilter": "priceRange:,100.5;"}
    assert (payload["sortField"], payload["sortValue"]) == ("", "")


def test_payload_sort_only():
    payload = build_payload(SearchQuery("键盘", sort="new"))
    assert payload["fromFilter"] is True
    assert (payload["sortField"], payload["sortValue"]) == ("create", "desc")
    assert payload["propValueStr"] == {"searchFilter": ""}


@pytest.mark.parametrize(
    "query, message",
    [
        (SearchQuery(" "), "搜索词"),
        (SearchQuery("x", sort="cheap"), "排序"),
        (SearchQuery("x", publish_days=5), "发布天数"),
        (SearchQuery("x", filters=("nope",)), "筛选"),
        (SearchQuery("x", price_min_cent=500, price_max_cent=100), "最低价"),
        (SearchQuery("x", price_min_cent=-1), "负数"),
    ],
)
def test_query_check(query, message):
    with pytest.raises(ValueError, match=message):
        query.check()


def test_card_parse():
    card = SearchCard.parse(make_row("1001", ori="¥199"))
    assert card is not None
    assert card.item_id == "1001"
    assert (card.price_cent, card.orig_price_cent) == (4990, 19900)
    assert card.want_count == 1434
    assert card.published_at == 1774544225
    assert card.free_ship is True
    # 图标标签不要，"N人想要"单独拿出来
    assert card.tags == ["几乎全新", "HP/惠普", "累计降价13%"]
    assert card.seller_tags == ["卖家信用极好"]
    assert (card.area, card.seller_nick) == ("广东", "外设小铺")
    assert card.url == "https://www.goofish.com/item?id=1001"


def test_card_parse_fallbacks():
    """点击参数里没有价格时拼卡片上显示的价格；没有想要标签、不包邮。"""
    row = make_row("1002", price="", want=None, tag_arg="new/credit-6", ori="1.2万")
    card = SearchCard.parse(row)
    assert card is not None
    assert card.price_cent == 4990
    assert card.orig_price_cent == 1200000
    assert card.want_count is None
    assert card.free_ship is False


def test_card_parse_skips_unknown_rows():
    assert SearchCard.parse({"data": {"template": {}}}) is None


def test_search_page():
    seen = []

    def handler(request):
        seen.append((request.url.params["api"], request.url.params["spm_cnt"], data_of(request)))
        return ok(result([make_row("1"), {"data": {}}, make_row("2")], found=1500))

    async def go():
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            return await SearchApi(mtop).search(SearchQuery("键盘"), page=3)

    page = run(go())
    assert [c.item_id for c in page.items] == ["1", "2"]
    assert (page.has_next, page.total) == (True, 1500)
    api, spm, body = seen[0]
    assert (api, spm) == (SEARCH_API, "a21ybx.search.0.0")
    assert (body["keyword"], body["pageNumber"]) == ("键盘", 3)


def test_search_checks_before_calling():
    def handler(request):
        raise AssertionError("不该请求平台")

    async def go(query, page=1):
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            return await SearchApi(mtop).search(query, page)

    with pytest.raises(ValueError, match="搜索词"):
        run(go(SearchQuery("")))
    with pytest.raises(ValueError, match="页码"):
        run(go(SearchQuery("键盘"), page=51))


def test_last_page_has_no_next():
    def handler(request):
        return ok(result([make_row("1")], next_page=True))

    async def go():
        async with MtopClient(
            Credential(cookies={"_m_h5_tk": "t_1"}), transport=httpx.MockTransport(handler)
        ) as mtop:
            return await SearchApi(mtop).search(SearchQuery("键盘"), page=50)

    assert run(go()).has_next is False
