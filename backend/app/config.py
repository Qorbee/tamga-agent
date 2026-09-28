"""Settings — the only module that reads the environment.

Everything else calls ``get_settings()``. A required value with no default fails when the app
starts, not on the first request that needs it (root ``AGENTS.md``, contract 1).

A new variable goes here, in ``.env.example`` with a comment, and nowhere else.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from this file rather than the working directory, so `uv run` from anywhere, Alembic and
# the test runner all read the same `.env`. Real environment variables win over the file.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


LOCAL_WEB_URL = "http://localhost:3000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str = "tamga"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: PostgresDsn

    # Comma-separated, because a list-typed field would demand JSON in the environment.
    cors_origins: str = ""

    # The web app's address, for links the backend hands out (invites).
    web_url: str = LOCAL_WEB_URL

    # Sign-in (ADR-0004). An empty domain makes the cookie host-only, which is right locally; in
    # production it is the parent domain the web and API hosts share.
    session_cookie_domain: str = ""
    session_days: int = 30
    invite_days: int = 7

    # Guest sandboxes for judges: their lifetime, a per-client limit, and a global cap.
    guest_hours: int = 24
    guest_sessions_per_client_per_hour: int = 5
    # Sign-ins and invite acceptances that check a password, per client address.
    password_attempts_per_client_per_hour: int = 20
    max_active_sandboxes: int = 200

    @model_validator(mode="after")
    def _deployed_links_point_somewhere_real(self) -> Self:
        # The default suits a laptop; deployed, it would send every invite to localhost.
        if self.app_env in ("staging", "production") and self.web_url == LOCAL_WEB_URL:
            raise ValueError("set WEB_URL to the deployed web app's address")
        return self

    @property
    def database_url_libpq(self) -> str:
        """The same database for psycopg (the job queue's driver), which takes a plain libpq URL."""
        return str(self.database_url).replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # mypy cannot see that pydantic-settings fills the required fields from the environment.
    return Settings()  # type: ignore[call-arg]
