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
"""


def connect(path: str | Path = DEFAULT_PATH) -> sqlite3.Connection:
    """打开数据库并建表。传 ":memory:" 用内存库（测试用）。"""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
