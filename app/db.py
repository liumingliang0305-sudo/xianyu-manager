"""本地数据库：一个 SQLite 文件，默认 data/xianyu.db。

金额一律存分（整数），时间一律存秒级时间戳。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_PATH = Path("data/xianyu.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    account_id    TEXT    NOT NULL,          -- 账号 unb
    item_id       TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    price_cent    INTEGER NOT NULL,
    status        INTEGER NOT NULL,          -- 平台的 itemStatus：0 在售，1 已卖出
    category_id   INTEGER,
    pic_url       TEXT    NOT NULL DEFAULT '',
    want_count    INTEGER,
    browse_count  INTEGER,                   -- 浏览、收藏只有同步时带 --stats 才有
    collect_count INTEGER,
    on_platform   INTEGER NOT NULL DEFAULT 1, -- 最近一次同步时还在主页列表里
    first_seen_at REAL    NOT NULL,
    synced_at     REAL    NOT NULL,
    PRIMARY KEY (account_id, item_id)
);

-- 通过本系统做过的每次改动，成功失败都记
CREATE TABLE IF NOT EXISTS item_ops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT    NOT NULL,
    item_id    TEXT    NOT NULL,
    action     TEXT    NOT NULL,             -- price / desc / downshelf / delete
    detail     TEXT    NOT NULL,             -- JSON：改前改后
    ok         INTEGER NOT NULL,
    error      TEXT,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS item_ops_item ON item_ops (account_id, item_id);

-- 卖出的订单
CREATE TABLE IF NOT EXISTS orders (
    account_id    TEXT    NOT NULL,
    order_id      TEXT    NOT NULL,
    item_id       TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL DEFAULT '',
    pic_url       TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL,          -- 归一后的状态，见 xianyu_core/order.py
    status_text   TEXT    NOT NULL DEFAULT '', -- 平台原文
    price_cent    INTEGER,                   -- 订单金额
    quantity      INTEGER NOT NULL DEFAULT 1,
    buyer_id      TEXT    NOT NULL DEFAULT '',
    buyer_nick    TEXT    NOT NULL DEFAULT '',
    created_at    REAL,                      -- 下单时间
    raw           TEXT    NOT NULL DEFAULT '{}', -- 平台最近一次返回的原始数据（JSON）
    first_seen_at REAL    NOT NULL,
    synced_at     REAL    NOT NULL,
    PRIMARY KEY (account_id, order_id)
);

-- 通过本系统对订单做过的改动
CREATE TABLE IF NOT EXISTS order_ops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT    NOT NULL,
    order_id   TEXT    NOT NULL,
    action     TEXT    NOT NULL,             -- price
    detail     TEXT    NOT NULL,             -- JSON：改前改后
    ok         INTEGER NOT NULL,
    error      TEXT,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS order_ops_order ON order_ops (account_id, order_id);

-- 即时消息的会话（单聊），见 app/messages.py
CREATE TABLE IF NOT EXISTS conversations (
    account_id   TEXT    NOT NULL,
    cid          TEXT    NOT NULL,           -- 会话 ID，不带 @goofish
    peer_id      TEXT    NOT NULL DEFAULT '',
    peer_name    TEXT    NOT NULL DEFAULT '', -- 对方昵称
    peer_type    TEXT    NOT NULL DEFAULT '', -- 对方的用户类型，查昵称要用；普通用户是 "0"
    named_at     REAL,                        -- 上次向平台查对方昵称的时间，没查过是 NULL
    visible      INTEGER NOT NULL DEFAULT 1,  -- 网页版显示不显示（在 App 里删掉的会话是 0）
    muted        INTEGER NOT NULL DEFAULT 0,  -- 开了消息免打扰
    biz_type     TEXT    NOT NULL DEFAULT '', -- "1" 是商品会话，别的是系统通知之类
    item_id      TEXT    NOT NULL DEFAULT '',
    item_title   TEXT    NOT NULL DEFAULT '',
    item_pic     TEXT    NOT NULL DEFAULT '',
    order_id     TEXT    NOT NULL DEFAULT '',
    unread       INTEGER NOT NULL DEFAULT 0,  -- 平台的红点数，收到对方新消息时加一
    updated_at   REAL,                        -- 最后有动静的时间
    fetched_at   REAL,                        -- 最新消息补拉到了哪个时间点，没拉过是 NULL
    older_cursor INTEGER,                     -- 往前翻历史消息的游标（毫秒），没翻过是 NULL
    older_more   INTEGER,                     -- 平台上还有没有更早的消息，不知道是 NULL
    first_seen_at REAL   NOT NULL,
    PRIMARY KEY (account_id, cid)
);

CREATE TABLE IF NOT EXISTS messages (
    account_id   TEXT    NOT NULL,
    message_id   TEXT    NOT NULL,
    cid          TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL DEFAULT '',
    sender_name  TEXT    NOT NULL DEFAULT '',
    sent_at      REAL,
    content_type INTEGER,                     -- 1 是文本
    text         TEXT    NOT NULL DEFAULT '', -- 文本消息是正文，别的类型是平台给的摘要
    item_id      TEXT    NOT NULL DEFAULT '',
    raw          TEXT    NOT NULL DEFAULT '{}', -- 原始数据（JSON），去掉了发送端的设备信息
    received_at  REAL    NOT NULL,
    PRIMARY KEY (account_id, message_id)
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages (account_id, cid, sent_at);
"""


# 建表以后才加的列：(表, 列, 定义)。老库打开时补上
ADDED_COLUMNS = [
    ("conversations", "peer_type", "TEXT NOT NULL DEFAULT ''"),
    ("conversations", "named_at", "REAL"),
    ("conversations", "visible", "INTEGER NOT NULL DEFAULT 1"),
    ("conversations", "muted", "INTEGER NOT NULL DEFAULT 0"),
]


def connect(path: str | Path = DEFAULT_PATH) -> sqlite3.Connection:
    """打开数据库并建表，老库补上新加的列。传 ":memory:" 用内存库（测试用）。"""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_columns(conn)
    return conn


def _add_columns(conn: sqlite3.Connection) -> None:
    with conn:
        for table, column, definition in ADDED_COLUMNS:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column in columns:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if (table, column) == ("conversations", "named_at"):
                # 以前的 peer_name 取自会话扩展里的 squadName，那其实是商品的简称，不是昵称
                conn.execute("UPDATE conversations SET peer_name = ''")
