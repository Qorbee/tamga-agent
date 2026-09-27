"""Test harness.

It sets its own environment before the app is imported — the one place outside ``app/config.py``
allowed to, because a test database URL is exactly what the app's settings must never default to.
Real environment variables win, which is how CI points the suite at its service containers.
"""

import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/app_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Integration tests need docker compose's Postgres and Redis. They skip locally unless asked
    # for, and CI sets INTEGRATION=1 — a tier that only ever skips is not coverage.
    if os.environ.get("INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set INTEGRATION=1 with docker compose up to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
