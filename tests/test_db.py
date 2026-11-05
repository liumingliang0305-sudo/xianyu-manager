import json

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, pool

from app import db, schema


def test_migrations_match_schema(database_url):
    """迁移跑完的库和 app/schema.py 对得上：改了表结构忘了生成迁移，这里会报。"""
    engine = create_engine(db.sqlalchemy_url(database_url), poolclass=pool.NullPool)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), schema.metadata) == []


def test_connect_refuses_outdated_schema_then_works_after_upgrade(migrations, database_url):
    command.downgrade(migrations, "base")
    try:
        with pytest.raises(db.SchemaOutdatedError, match="alembic upgrade head"):
            db.connect(database_url)
    finally:
        command.upgrade(migrations, "head")
    db.connect(database_url).close()


def test_message_unread_count_filled_from_raw(migrations, database_url):
    """加 unread_count 的迁移：已经存下的消息从原始数据里取（历史消息、推送两种格式）。"""
    command.downgrade(migrations, "8f3a1d6c9b27")
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            for message_id, raw in [
                ("history", {"readStatus": 2, "message": {"messageId": "h", "unreadCount": 0}}),
                ("push", {"1": {"3": "p", "7": 2, "8": 1}, "3": {"needPush": "false"}}),
                ("sent", {"message": {"messageId": "s", "unreadCount": None}}),
                ("bare", {}),
            ]:
                conn.execute(
                    "INSERT INTO messages (account_id, message_id, cid, raw, received_at)"
                    " VALUES ('42', %s, '777', %s, 0)",
                    (message_id, json.dumps(raw)),
                )
    finally:
        command.upgrade(migrations, "head")
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT message_id, unread_count FROM messages").fetchall()
    assert dict(rows) == {"history": 0, "push": 1, "sent": None, "bare": None}


def test_reconnects_after_connection_is_lost(database):
    pid = database.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with psycopg.connect(database.url, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    # 断的那一下照样报错，下一次重连
    with pytest.raises(psycopg.OperationalError):
        database.execute("SELECT 1")
    assert database.execute("SELECT 1 AS one").fetchone() == {"one": 1}


def test_transaction_rolls_back_on_error(database):
    with pytest.raises(RuntimeError), database.transaction():
        database.execute(
            "INSERT INTO items (account_id, item_id, title, price_cent, status, first_seen_at,"
            " synced_at) VALUES ('1', '1001', '洗衣机', 100, 0, 0, 0)"
        )
        raise RuntimeError
    assert database.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0
