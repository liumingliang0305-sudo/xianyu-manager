"""一次性导入：以前 SQLite 本地库里的数据搬进 PostgreSQL。"""

import sqlite3

import pytest
from fakes import UNB

from app import schema
from app.items import ItemService
from app.messages import MessageStore
from app.sqlite_import import import_sqlite

ROWS = {
    "items": [
        dict(account_id=UNB, item_id="1001", title="洗衣机", price_cent=35800, status=0,
             category_id=201221406, pic_url="", want_count=3, browse_count=None,
             collect_count=None, on_platform=0, first_seen_at=1761700000.5,
             synced_at=1761800000.25),
    ],
    "item_ops": [
        dict(id=1, account_id=UNB, item_id="1001", action="price", detail='{"from": 1, "to": 2}',
             ok=1, error=None, created_at=1761700001.0),
        dict(id=5, account_id=UNB, item_id="1001", action="delete", detail="{}", ok=0,
             error="平台报错", created_at=1761700002.0),
    ],
    "conversations": [
        dict(account_id=UNB, cid="777", peer_id="200", peer_name="小林同学", peer_type="0",
             named_at=None, visible=1, muted=1, biz_type="1", item_id="1001", item_title="洗衣机",
             item_pic="", order_id="", unread=2, updated_at=1761700003.0, fetched_at=None,
             older_cursor=1792069831032, older_more=1, first_seen_at=1761700000.0),
        dict(account_id=UNB, cid="778", peer_id="201", peer_name="", peer_type="", named_at=None,
             visible=0, muted=0, biz_type="1", item_id="", item_title="", item_pic="",
             order_id="", unread=0, updated_at=None, fetched_at=None, older_cursor=None,
             older_more=None, first_seen_at=1761700000.0),
    ],
    "messages": [
        dict(account_id=UNB, message_id="m1", cid="777", sender_id="200", sender_name="小林同学",
             sent_at=1761700003.0, content_type=1, text="还在吗", item_id="1001", raw="{}",
             received_at=1761700003.5),
    ],
}  # fmt: skip


def make_sqlite(path, rows, *, drop=()):
    """SQLite 版的库。列不写类型（SQLite 本来也不管），`drop` 里的列不建，模拟老库。"""
    conn = sqlite3.connect(path)
    for table in schema.metadata.sorted_tables:
        names = [c.name for c in table.columns if (table.name, c.name) not in drop]
        conn.execute(f"CREATE TABLE {table.name} ({', '.join(names)})")
        for row in rows.get(table.name, []):
            values = [row[n] for n in names]
            marks = ", ".join("?" * len(names))
            conn.execute(f"INSERT INTO {table.name} ({', '.join(names)}) VALUES ({marks})", values)
    conn.commit()
    conn.close()
    return path


def test_imports_everything_and_converts_types(tmp_path, database, accounts):
    path = make_sqlite(tmp_path / "xianyu.db", ROWS)
    counts = import_sqlite(path, database)
    assert counts == {
        "items": 1, "item_ops": 2, "orders": 0, "order_ops": 0, "conversations": 2, "messages": 1,
    }  # fmt: skip

    items = ItemService(database, accounts)
    [item] = items.list(UNB, include_gone=True)
    assert (item.on_platform, item.first_seen_at) == (False, 1761700000.5)
    assert [(op.id, op.ok) for op in items.history(UNB)] == [(5, False), (1, True)]
    # 自增 ID 接着最大的往下排
    new_id = database.execute(
        "INSERT INTO item_ops (account_id, item_id, action, detail, ok, created_at)"
        " VALUES (%s, '1001', 'desc', '{}', true, 0) RETURNING id",
        (UNB,),
    ).fetchone()["id"]
    assert new_id == 6

    store = MessageStore(database)
    assert [c.cid for c in store.conversations(UNB)] == ["777"]  # 778 在 App 里删掉了
    assert store.older(UNB, "777") == (1792069831032, True)
    assert store.conversation(UNB, "778").older_more is None
    assert [m.text for m in store.messages(UNB, "777")[0]] == ["还在吗"]


def test_only_imports_into_empty_database(tmp_path, database):
    path = make_sqlite(tmp_path / "xianyu.db", ROWS)
    import_sqlite(path, database)
    with pytest.raises(ValueError, match="已经有数据"):
        import_sqlite(path, database)


def test_old_sqlite_missing_columns_imports_nothing(tmp_path, database):
    path = make_sqlite(tmp_path / "xianyu.db", ROWS, drop={("conversations", "muted")})
    with pytest.raises(ValueError, match="muted"):
        import_sqlite(path, database)
    # 前面几张表也回滚了
    assert database.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0


def test_missing_file(tmp_path, database):
    with pytest.raises(ValueError, match="没有这个文件"):
        import_sqlite(tmp_path / "nope.db", database)
