"""Alembic 运行环境。连接串：调用方放进 config.attributes["url"]（测试这样用），
不然取 app/db.py 的 default_url()（环境变量 DATABASE_URL，没设时连本机 docker 里的库）。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db import default_url, sqlalchemy_url
from app.schema import metadata

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

url = sqlalchemy_url(config.attributes.get("url") or default_url())


def run_migrations_offline() -> None:
    """--sql：只输出 SQL，不连库（给数据库管理员审、或者手工执行时用）。"""
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
