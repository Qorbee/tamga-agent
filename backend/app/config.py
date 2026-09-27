"""Settings — the only module that reads the environment.

Everything else calls ``get_settings()``. A required value with no default fails when the app
starts, not on the first request that needs it (root ``AGENTS.md``, contract 1).

A new variable goes here, in ``.env.example`` with a comment, and nowhere else.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from this file rather than the working directory, so `uv run` from anywhere, Alembic and
# the test runner all read the same `.env`. Real environment variables win over the file.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str = "backend"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: PostgresDsn
    redis_url: RedisDsn

    # Comma-separated, because a list-typed field would demand JSON in the environment.
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # mypy cannot see that pydantic-settings fills the required fields from the environment.
    return Settings()  # type: ignore[call-arg]
