"""本地数据库：PostgreSQL。连接串取环境变量 DATABASE_URL，没设时连本机 docker 里的
（docker-compose.yml）。

表结构在 app/schema.py，改表走 Alembic 迁移（migrations/）。先 `uv run alembic upgrade head`
再启动；库没升到最新时 connect() 直接报错，免得跑到一半才发现少了列。

金额一律存分（整数），时间一律存秒级时间戳。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

LOCAL_URL = "postgresql://xianyu:xianyu@localhost:5432/xianyu"
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

Row = dict[str, Any]


class SchemaOutdatedError(RuntimeError):
    """库的表结构和代码对不上：还没跑迁移，或者代码比库旧。"""


def default_url() -> str:
    return os.environ.get("DATABASE_URL") or LOCAL_URL


def sqlalchemy_url(url: str) -> str:
    """Alembic 走 SQLAlchemy，连接串要写明用 psycopg 3。"""
    scheme, _, rest = url.partition("://")
    if scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg://{rest}"
    return url


class Database:
    """一条连接，所有服务共用。只在事件循环线程上用，同一时间只有一处在用。

    自动提交；几条语句要一起成功的放进 `with db.transaction():`。连接断了（数据库重启、网络断），
    下次用的时候重连，断的那一下照样报错。
    """

    def __init__(self, url: str):
        self.url = url
        self._conn = self._connect()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> psycopg.Cursor[Row]:
        return self._live().execute(sql, params)

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> None:
        with self._live().cursor() as cursor:
            cursor.executemany(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._live().transaction():
            yield

    def close(self) -> None:
        self._conn.close()

    def _live(self) -> psycopg.Connection[Row]:
        if self._conn.closed:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> psycopg.Connection[Row]:
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)


def connect(url: str | None = None) -> Database:
    """连上数据库，确认表结构是最新的。`url` 不给时用 default_url()。"""
    db = Database(url or default_url())
    try:
        check_schema(db)
    except Exception:
        db.close()
        raise
    return db


def check_schema(db: Database) -> None:
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(MIGRATIONS)).get_current_head()
    try:
        row = db.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.errors.UndefinedTable:
        row = None
    current = row["version_num"] if row else None
    if current != head:
        raise SchemaOutdatedError(
            f"数据库表结构不是最新的（库里 {current or '还没建表'}，代码要 {head}），"
            "先运行 uv run alembic upgrade head"
        )
