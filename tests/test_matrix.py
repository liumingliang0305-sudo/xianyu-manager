"""账号矩阵：资料、备注、概况、移除账号。"""

import httpx
import pytest
from fakes import UNB, add_user, sign_in
from fastapi.testclient import TestClient

from app import db
from app.api import create_app
from app.matrix import PROFILE_TTL
from xianyu_core.credential import Credential
from xianyu_core.guard import Guard
from xianyu_core.profile import parse_profile

PROFILE_API = "mtop.idle.web.user.page.head"
OTHER = "43"


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


def profile_calls(platform):
    return sum(api == PROFILE_API for api, _ in platform.calls)


def add_account(accounts, database_url, account_id=OTHER, username="tester"):
    Credential(cookies={"unb": account_id, "_m_h5_tk": "tok_1"}).save(accounts.path(account_id))
    add_user(database_url, username=username, accounts=[account_id])


def test_parse_profile():
    profile = parse_profile(
        {
            "module": {
                "base": {
                    "displayName": "海边杂货铺",
                    "avatar": {"avatar": "http://img.alicdn.com/a.jpg"},
                    "ipLocation": "广东省",
                    "ylzTags": [
                        {"code": "cs_buyer_level", "text": "买家信用良好"},
                        {"code": "cs_seller_level", "text": "卖家信用极好"},
                    ],
                },
                "social": {"followers": "1,203", "following": "8"},
            }
        }
    )
    assert profile.nick == "海边杂货铺"
    assert profile.avatar == "https://img.alicdn.com/a.jpg"
    assert profile.seller_credit == "卖家信用极好"
    assert profile.followers == 1203
    assert parse_profile({}).nick == ""
    assert parse_profile({}).followers is None


def test_accounts_carry_profile_and_fetch_it_once(client, platform):
    platform.nicknames[UNB] = "主号小铺"
    account = client.get("/api/accounts").json()["accounts"][0]
    assert account["profile"]["nick"] == "主号小铺"
    assert account["profile"]["avatar"] == f"https://img.alicdn.com/bao/uploaded/{UNB}.jpg"
    assert account["profile"]["seller_credit"] == "卖家信用极好"
    assert account["profile"]["followers"] == 3
    assert account["profile"]["fetched_at"] is not None
    assert account["remark"] == ""
    assert account["stats"] == {"on_sale": 0, "to_ship": 0, "unread": 0}
    assert account["im"]["state"] == "off"
    assert account["added_at"] is not None

    client.get("/api/accounts")
    assert profile_calls(platform) == 1  # 一天以内不再拉

    # 手动重新检查时顺带重拉
    platform.nicknames[UNB] = "改名了"
    assert client.post(f"/api/accounts/{UNB}/check").json()["profile"]["nick"] == "改名了"
    assert profile_calls(platform) == 2


def test_profile_is_refetched_after_ttl(client, platform, database_url):
    client.get("/api/accounts")
    conn = db.connect(database_url)
    conn.execute("UPDATE account_profiles SET fetched_at = fetched_at - %s", (PROFILE_TTL + 1,))
    conn.close()
    client.get("/api/accounts")
    assert profile_calls(platform) == 2


def test_profile_failure_does_not_break_list_and_backs_off(client, platform):
    platform.fail[PROFILE_API] = "FAIL_BIZ_SYSTEM_ERROR::系统繁忙"
    account = client.get("/api/accounts").json()["accounts"][0]
    assert account["login"]["state"] == "valid"
    assert account["profile"]["nick"] == ""
    assert account["profile"]["fetched_at"] is None

    client.get("/api/accounts")
    assert profile_calls(platform) == 1  # 失败了不马上重试


def test_no_profile_fetch_when_login_expired(client, platform):
    platform.session_expired = True
    platform.renew_ok = False
    account = client.get("/api/accounts").json()["accounts"][0]
    assert account["login"]["state"] == "expired"
    assert profile_calls(platform) == 0


def test_stats_count_local_records(client, database_url):
    conn = db.connect(database_url)
    now = 1.0
    for item_id, status, on_platform in [
        ("1", 0, True),
        ("2", 0, True),
        ("3", 1, True),
        ("4", 0, False),
    ]:
        conn.execute(
            "INSERT INTO items (account_id, item_id, title, price_cent, status, on_platform,"
            " first_seen_at, synced_at) VALUES (%s, %s, '', 100, %s, %s, %s, %s)",
            (UNB, item_id, status, on_platform, now, now),
        )
    for order_id, side, status in [
        ("a", "sold", "to_ship"),
        ("b", "bought", "to_ship"),
        ("c", "sold", "done"),
    ]:
        conn.execute(
            "INSERT INTO orders (account_id, order_id, side, status, first_seen_at, synced_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (UNB, order_id, side, status, now, now),
        )
    for cid, unread, visible in [("x", 2, True), ("y", 3, True), ("z", 9, False)]:
        conn.execute(
            "INSERT INTO conversations (account_id, cid, unread, visible, first_seen_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (UNB, cid, unread, visible, now),
        )
    conn.close()
    stats = client.get("/api/accounts").json()["accounts"][0]["stats"]
    assert stats == {"on_sale": 2, "to_ship": 1, "unread": 5}


def test_accounts_listed_in_the_order_they_were_added(client, accounts, database_url, platform):
    add_account(accounts, database_url)
    listed = client.get("/api/accounts").json()["accounts"]
    assert [a["id"] for a in listed] == [UNB, OTHER]
    assert [a["profile"]["nick"] for a in listed] == [f"鱼友{UNB}", f"鱼友{OTHER}"]
    assert all(a["login"]["state"] == "valid" for a in listed)


def test_set_and_clear_remark(client):
    body = client.put(f"/api/accounts/{UNB}/remark", json={"remark": "  主号  数码 "}).json()
    assert body["remark"] == "主号 数码"
    assert client.get("/api/accounts").json()["accounts"][0]["remark"] == "主号 数码"

    resp = client.put(f"/api/accounts/{UNB}/remark", json={"remark": "长" * 21})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid"

    assert client.put(f"/api/accounts/{UNB}/remark", json={"remark": ""}).json()["remark"] == ""


def test_remove_account_keeps_local_data(client, accounts, database_url):
    client.put(f"/api/accounts/{UNB}/remark", json={"remark": "主号"})
    conn = db.connect(database_url)
    conn.execute(
        "INSERT INTO items (account_id, item_id, title, price_cent, status, first_seen_at,"
        " synced_at) VALUES (%s, '1', '', 100, 0, 1, 1)",
        (UNB,),
    )

    assert client.delete(f"/api/accounts/{UNB}").json() == {"ok": True}

    assert not accounts.path(UNB).exists()
    assert client.get("/api/accounts").json() == {"accounts": []}
    assert client.get(f"/api/accounts/{UNB}/items").status_code == 404
    assert conn.execute("SELECT count(*) AS n FROM account_owners").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM account_profiles").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM items").fetchone()["n"] == 1
    conn.close()


def test_cannot_touch_other_users_accounts(client, accounts, database_url):
    add_account(accounts, database_url, username="someone")
    assert [a["id"] for a in client.get("/api/accounts").json()["accounts"]] == [UNB]
    assert client.delete(f"/api/accounts/{OTHER}").status_code == 404
    assert client.put(f"/api/accounts/{OTHER}/remark", json={"remark": "x"}).status_code == 404
    assert accounts.path(OTHER).exists()
