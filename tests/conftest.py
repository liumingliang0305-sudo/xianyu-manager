"""共用 fixture：假平台、一个已登录账号、测试库。

测试库默认是本机 docker 里同一个 PostgreSQL 上的 xianyu_test（docker compose up -d），
没有就建；每次跑测试先清空、从头跑一遍迁移，每个用例开始前清空所有表。
换库用环境变量 TEST_DATABASE_URL。
"""

import os

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fakes import UNB, FakePlatform
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import db, schema
from app.accounts import Accounts
from xianyu_core import renew
from xianyu_core.credential import Credential

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://xianyu:xianyu@localhost:5432/xianyu_test"
)


def alembic_config(url: str) -> Config:
    config = Config(str(db.MIGRATIONS.parent / "alembic.ini"))
    config.attributes["url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_url():
    name = conninfo_to_dict(TEST_URL)["dbname"]
    if not name.endswith("_test"):
        pytest.fail(f"测试会清空整个库，库名要以 _test 结尾：{name}", pytrace=False)
    try:
        with psycopg.connect(make_conninfo(TEST_URL, dbname="postgres"), autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
    except psycopg.OperationalError as exc:
        pytest.fail(f"连不上测试库 {name}，先 docker compose up -d：{exc}", pytrace=False)
    command.upgrade(alembic_config(TEST_URL), "head")
    return TEST_URL


@pytest.fixture
def migrations(migrated_url):
    """测试库的 Alembic 配置，用例里升级、降级用。"""
    return alembic_config(migrated_url)


@pytest.fixture
def database_url(migrated_url):
    """清空了的测试库。"""
    tables = sql.SQL(", ").join(sql.Identifier(t.name) for t in schema.metadata.sorted_tables)
    with psycopg.connect(migrated_url, autocommit=True) as conn:
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(tables))
    return migrated_url


@pytest.fixture
def database(database_url):
    conn = db.connect(database_url)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def no_renew_delay(monkeypatch):
    """免密续期前不用真等 2 秒。"""
    monkeypatch.setattr(renew, "SILENT_RENEW_DELAY", 0)


@pytest.fixture
def platform():
    return FakePlatform(
        {
            "1001": {"title": "洗衣机", "price": 35800, "status": 0},
            "1002": {"title": "平板", "price": 140000, "status": 1},
        },
        {
            "5001": {
                "item_id": "1002",
                "status": "交易成功",
                "price": 140000,
                "post": 0,
                "created": 1761700000000,
            },
            "5002": {
                "item_id": "1001",
                "status": "等待买家付款",
                "price": 35800,
                "post": 1000,
                "created": 1761800000000,
            },
        },
    )


@pytest.fixture
def accounts(tmp_path, platform):
    Credential(cookies={"unb": UNB, "_m_h5_tk": "tok_1"}).save(tmp_path / f"{UNB}.json")
    return Accounts(tmp_path, transport=httpx.MockTransport(platform))
