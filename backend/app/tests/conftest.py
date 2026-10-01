"""Test harness.

It sets its own environment before the app is imported — the one place outside ``app/config.py``
allowed to, because a test database URL is exactly what the app's settings must never default to.
Real environment variables win, which is how CI points the suite at its service containers.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/app_test"
)
# The SeaweedFS service in docker-compose.yml, in a bucket of the tests' own.
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:8333")
os.environ.setdefault("S3_BUCKET", "tamga-test")
os.environ.setdefault("S3_ACCESS_KEY_ID", "local")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "local")
# A key the model layer can build a client with; the tests never reach the provider (below).
os.environ.setdefault("OPENAI_API_KEY", "test-key-never-sent")

import pydantic_ai.models  # noqa: E402

from app.config import get_settings  # noqa: E402  (the environment above must be set first)
from app.main import create_app  # noqa: E402
from app.platform.db import Base, dispose_db, get_session, import_all_models, init_db  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# No test calls a real model: an agent under test runs on a test model through Agent.override.
pydantic_ai.models.ALLOW_MODEL_REQUESTS = False


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
    # Every test empties every table: a DATABASE_URL copied from backend/.env would empty the one
    # you develop against.
    name = make_url(str(get_settings().database_url)).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"the tests empty their database; {name!r} does not end in _test", returncode=2)
    # No ini file: alembic.ini's logging setup would disable the app's loggers for the whole run.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    command.upgrade(config, "head")


@pytest.fixture
async def database(migrated_database: None) -> AsyncIterator[None]:
    """An empty database for each test, and the app's engine pointed at it."""
    url = str(get_settings().database_url)
    import_all_models()
    # procrastinate_jobs has no model; CASCADE takes its events and periodic defers with it.
    tables = ", ".join(
        [*(table.name for table in Base.metadata.sorted_tables), "procrastinate_jobs"]
    )
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


@pytest.fixture(scope="session")
def storage_bucket() -> None:
    """SeaweedFS makes no bucket by itself (production's is created in Cloudflare), so the tests
    make theirs."""
    settings = get_settings()
    secret = settings.s3_secret_access_key
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
        region_name=settings.s3_region,
    )
    try:
        client.create_bucket(Bucket=str(settings.s3_bucket))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            raise


@pytest.fixture(autouse=True)
def fresh_rate_limits() -> Iterator[None]:
    # Every test client is 127.0.0.1, so one test's attempts would count against the next.
    from app.modules.identity import service

    service._password_limiter.cache_clear()
    service._guest_limiter.cache_clear()
    yield


async def guest_client(client: httpx.AsyncClient) -> uuid.UUID:
    """Signs ``client`` in as the owner of a fresh sandbox; returns the sandbox's company id."""
    response = await client.post("/sessions/guest")
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["company"]["id"])
