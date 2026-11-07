import httpx
import pytest
from fakes import PASSWORD, UNB, add_user, sign_in
from fastapi.testclient import TestClient

from app.api import create_app
from app.stash import MAX_TAGS, StashedItem
from xianyu_core.guard import Guard

URL = "/api/search/stash"


@pytest.fixture
def client(tmp_path, accounts, platform, database_url):
    app = create_app(
        database_url=database_url,
        guard=Guard(),
        accounts_dir=accounts.root,
        images_dir=tmp_path / "images",
        transport=httpx.MockTransport(platform),
        interval=0,
        web_dist=None,
    )
    add_user(database_url, accounts=[UNB])
    with TestClient(app) as client:
        sign_in(client)
        yield client


def card(item_id="1001", **change):
    """搜索接口返回的一条（页面原样发回来），加上搜索词。"""
    body = {
        "item_id": item_id,
        "title": "九成新洗衣机 自提",
        "price_cent": 35800,
        "orig_price_cent": 99900,
        "pic_url": "http://img.alicdn.com/bao/uploaded/i4/1.heic",
        "area": "广东",
        "seller_nick": "外设小铺",
        "want_count": 7,
        "published_at": 1774544225.0,
        "free_ship": True,
        "tags": ["几乎全新", "累计降价13%"],
        "seller_tags": ["卖家信用极好"],
        "url": f"https://www.goofish.com/item?id={item_id}",
        "keyword": "洗衣机",
    }
    return {**body, **change}


def test_stash_list_and_remove_without_asking_xianyu(client, platform):
    sent = len(platform.calls)
    resp = client.post(URL, json=card())
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] is True
    assert body["item"]["keyword"] == "洗衣机"
    assert body["item"]["url"] == "https://www.goofish.com/item?id=1001"
    client.post(URL, json=card("1002", keyword="平板"))

    items = client.get(URL).json()["items"]
    assert [it["item_id"] for it in items] == ["1002", "1001"]  # 最近暂存的在前
    first = items[1]
    assert first["price_cent"] == 35800
    assert first["tags"] == ["几乎全新", "累计降价13%"]
    assert first["free_ship"] is True
    assert first["saved_at"] > 0

    assert client.delete(f"{URL}/1001").json() == {"removed": True}
    assert client.delete(f"{URL}/1001").json() == {"removed": False}  # 连点两下不报错
    assert [it["item_id"] for it in client.get(URL).json()["items"]] == ["1002"]
    assert len(platform.calls) == sent


def test_stash_again_updates_and_keeps_saved_time(client):
    first = client.post(URL, json=card()).json()["item"]
    again = client.post(URL, json=card(price_cent=30000, want_count=9, keyword="滚筒")).json()
    assert again["added"] is False
    assert again["item"]["price_cent"] == 30000
    assert again["item"]["want_count"] == 9
    assert again["item"]["keyword"] == "滚筒"
    assert again["item"]["saved_at"] == first["saved_at"]
    assert again["item"]["updated_at"] >= first["updated_at"]
    assert len(client.get(URL).json()["items"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"item_id": "abc"},
        {"item_id": ""},
        {"title": "  "},
        {"price_cent": -1},
        {"pic_url": "javascript:alert(1)"},
    ],
)
def test_bad_card_is_400(client, change):
    resp = client.post(URL, json=card(**change))
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"
    assert client.get(URL).json()["items"] == []


def test_clean_trims_and_caps():
    item = StashedItem.clean(
        card(
            title="  洗衣机  ",
            orig_price_cent=0,
            want_count=None,
            published_at=None,
            tags=["", " 全新 ", *[f"t{i}" for i in range(40)]],
            free_ship="yes",
        )
    )
    assert item.title == "洗衣机"
    assert item.orig_price_cent is None
    assert item.want_count is None
    assert item.published_at is None
    assert item.tags[0] == "全新"
    assert len(item.tags) == MAX_TAGS
    assert item.free_ship is False


def test_stash_belongs_to_the_user(client, database_url):
    client.post(URL, json=card())
    add_user(database_url, username="other")
    resp = client.post("/api/auth/login", json={"username": "other", "password": PASSWORD})
    other = {"authorization": f"Bearer {resp.json()['access_token']}"}

    assert client.get(URL, headers=other).json()["items"] == []
    assert client.delete(f"{URL}/1001", headers=other).json() == {"removed": False}
    assert len(client.get(URL).json()["items"]) == 1


def test_stash_needs_sign_in(client):
    client.headers.pop("authorization", None)
    assert client.get(URL).status_code == 401
    assert client.post(URL, json=card()).status_code == 401
