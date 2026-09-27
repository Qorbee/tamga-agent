"""Alembic environment: async engine, URL from settings, models found by walking ``app/modules``."""

import asyncio
import importlib
import pkgutil
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.modules
from app.config import get_settings
from app.platform.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def import_all_models() -> None:
    """Import every ``app/modules/<name>/models.py`` so autogenerate sees every table.

    Derived from the filesystem, so a new module needs no registration here — a model autogenerate
    cannot see is a table it proposes to drop.
    """
    for module in pkgutil.iter_modules(app.modules.__path__):
        if not module.ispkg:
            continue
        name = f"app.modules.{module.name}.models"
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise


import_all_models()
target_metadata = Base.metadata
database_url = str(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
