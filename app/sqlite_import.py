"""一次性：把以前 SQLite 本地库（data/xianyu.db）里的数据导进 PostgreSQL。

只往空库里导，导一半出错整个回滚。SQLite 里的 0/1 换成布尔，改动记录的自增 ID 原样搬过去，
再把 ID 序列拨到最大值后面。导完原来的 SQLite 文件不动，确认没问题再自己删。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app import schema
from app.db import Database

TABLES = ["items", "item_ops", "orders", "order_ops", "conversations", "messages"]
# 带自增 ID 的表
IDENTITY = ["item_ops", "order_ops"]


def import_sqlite(path: str | Path, db: Database) -> dict[str, int]:
    """返回每张表导了几行。"""
    if not Path(path).is_file():
        raise ValueError(f"没有这个文件：{path}")
    src = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        return _copy(src, db)
    finally:
        src.close()


def _copy(src: sqlite3.Connection, db: Database) -> dict[str, int]:
    counts: dict[str, int] = {}
    with db.transaction():
        for table in TABLES:
            if db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ValueError(f"PostgreSQL 里的 {table} 已经有数据，只能导进空库")
        for table in TABLES:
            columns = schema.metadata.tables[table].columns
            names = [c.name for c in columns]
            have = {row[1] for row in src.execute(f"PRAGMA table_info({table})")}
            if missing := [n for n in names if n not in have]:
                raise ValueError(
                    f"SQLite 库太旧，{table} 少了 {', '.join(missing)}："
                    "先用迁移到 PostgreSQL 之前的版本跑一次 xym serve 补上列"
                )
            booleans = [i for i, c in enumerate(columns) if c.type.python_type is bool]
            rows = []
            for row in src.execute(f"SELECT {', '.join(names)} FROM {table}"):
                row = list(row)
                for i in booleans:
                    if row[i] is not None:
                        row[i] = bool(row[i])
                rows.append(row)
            marks = ", ".join(["%s"] * len(names))
            db.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})", rows)
            counts[table] = len(rows)
        for table in IDENTITY:
            db.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                f" COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )
    return counts
