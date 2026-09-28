"""Test harness.

It sets its own environment before the app is imported — the one place outside ``app/config.py``
allowed to, because a test database URL is exactly what the app's settings must never default to.
Real environment variables win, which is how CI points the suite at its service containers.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/app_test"
)

from app.config import get_settings  # noqa: E402  (the environment above must be set first)
from app.main import create_app  # noqa: E402
from app.platform.db import Base, dispose_db, get_session, import_all_models, init_db  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Integration tests need docker compose's Postgres. They skip locally unless asked
    # for, and CI sets INTEGRATION=1 — a tier that only ever skips is not coverage.
    if os.environ.get("INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set INTEGRATION=1 with docker compose up to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """The test database at the newest revision, built by the migrations as production is."""
    # No ini file: alembic.ini's logging setup would disable the app's loggers for the whole run.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    command.upgrade(config, "head")


@pytest.fixture
async def database(migrated_database: None) -> AsyncIterator[None]:
    """An empty database for each test, and the app's engine pointed at it."""
    url = str(get_settings().database_url)
    import_all_models()
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    await engine.dispose()

    init_db(url)
    yield
    await dispose_db()


@pytest.fixture
async def db(database: None) -> AsyncIterator[AsyncSession]:
    """A session for arranging state through services, as a request would get one."""
    async for session in get_session():
        yield session


@pytest.fixture
async def client(database: None) -> AsyncIterator[httpx.AsyncClient]:
    # https, so the client keeps the Secure session cookie as a browser would.
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client
