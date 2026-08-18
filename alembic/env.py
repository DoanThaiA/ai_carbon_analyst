"""
Alembic env cho async engine (SQLAlchemy 2.0 + asyncpg). sqlalchemy.url
KHÔNG lấy từ alembic.ini mà từ core.config.Settings — cùng nguồn
DATABASE_URL (.env) với phần còn lại của app, để không phải khai 2 nơi rồi
lệch nhau giữa dev/staging/prod.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from core.config import Settings
from db.base import Base
from db.session import to_asyncpg_dsn

# import để các model đăng ký vào Base.metadata (chỉ import, không dùng trực tiếp)
from db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", to_asyncpg_dsn(Settings.from_env().database_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
