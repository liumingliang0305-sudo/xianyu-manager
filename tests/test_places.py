import asyncio

import httpx
import pytest
from fakes import FakePlatform

from app import places
from app.drafts import address_option
from app.places import PlaceSearch, PlacesUnavailableError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake():
    return FakePlatform({})


def search(fake, keyword, key="k"):
    return run(PlaceSearch(key, transport=httpx.MockTransport(fake)).search(keyword))


def test_search_returns_xianyu_address_format(fake):
    station, street = search(fake, "北京")  # 只是一个城市、没有坐标的不要
    assert station == {
        "poi": "北京站",
        "poiId": "B000A7BD6C",
        "address": "毛家湾胡同甲13号",
        "prov": "北京市",
        "city": "北京市",
        "area": "东城区",
        "divisionId": 110101,
        "longitude": "116.427287",
        "latitude": "39.903030",
    }
    assert street["address"] == ""  # 高德没有值时给空数组
    [query] = fake.amap_queries
    assert (query["key"], query["keywords"], query["offset"]) == ("k", "北京", "10")
    assert query["extensions"] == "all"  # base 不给 adcode


def test_option_for_the_draft(fake):
    station, street = search(fake, "北京")
    option = address_option(station)
    assert option["detail"] == "北京市东城区毛家湾胡同甲13号"  # 直辖市不重复写
    assert option["address"] == {
        "area": "东城区",
        "city": "北京市",
        "divisionId": 110101,
        "gps": "39.903030,116.427287",
        "poiId": "B000A7BD6C",
        "poiName": "北京站",
        "prov": "北京市",
    }
    assert address_option(street)["detail"] == "广东省广州市越秀区"


def test_blank_keyword_does_not_call_amap(fake):
    assert search(fake, "  ") == []
    assert fake.amap_queries == []


def test_errors(fake):
    with pytest.raises(PlacesUnavailableError, match="没配高德 key"):
        search(fake, "北京", key=None)
    fake.amap_info = "USERKEY_PLAT_NOMATCH"
    with pytest.raises(PlacesUnavailableError, match="USERKEY_PLAT_NOMATCH"):
        search(fake, "北京")


def test_load_key_prefers_env(tmp_path, monkeypatch):
    path = tmp_path / "amap.key"
    monkeypatch.delenv("AMAP_KEY", raising=False)
    assert places.load_key(path) is None
    path.write_text("from-file\n")
    assert places.load_key(path) == "from-file"
    monkeypatch.setenv("AMAP_KEY", "from-env")
    assert places.load_key(path) == "from-env"
