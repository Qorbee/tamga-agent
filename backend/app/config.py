"""Settings — the only module that reads the environment.

Everything else calls ``get_settings()``. A required value with no default fails when the app
starts, not on the first request that needs it (root ``AGENTS.md``, contract 1).

A new variable goes here, in ``.env.example`` with a comment, and nowhere else.
"""

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from this file rather than the working directory, so `uv run` from anywhere, Alembic and
# the test runner all read the same `.env`. Real environment variables win over the file.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


LOCAL_WEB_URL = "http://localhost:3000"


class Mailbox(BaseModel):
    """One IMAP mailbox Tamga reads, never writes (ADR-0008)."""

    host: str
    port: int = 993
    username: str
    password: SecretStr
    # Plain IMAP only for the tests' fake server: a deployment refuses it, as it would send the
    # password in the clear.
    tls: bool = True


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

    # Private file storage over the S3 API (ADR-0001): Cloudflare R2 in production, SeaweedFS from
    # docker-compose.yml locally. Optional locally, so tools that never touch files (Alembic, the
    # OpenAPI dump) run without it; required to start in staging and production.
    s3_endpoint_url: str | None = None
    # The same endpoint as a browser reaches it, for presigned URLs, when that differs.
    s3_public_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_region: str = "auto"

    # The pilot's mailboxes, read into the pilot company (ADR-0008). Until a per-company connections
    # table exists, they are settings: a JSON list in MAILBOXES.
    pilot_company_id: uuid.UUID | None = None
    mailboxes: list[Mailbox] = []
    # How far back the first poll of a folder reads, and how many messages a poll takes per folder.
    mail_backfill_days: int = Field(default=90, gt=0)
    # Zero would read nothing, yet move a new folder's cursor past all of its mail.
    mail_batch: int = Field(default=200, gt=0)

    # Guest sandboxes for judges: their lifetime, a per-client limit, and a global cap.
    guest_hours: int = 24
    guest_sessions_per_client_per_hour: int = 5
    # Sign-ins and invite acceptances that check a password, per client address.
    password_attempts_per_client_per_hour: int = 20
    max_active_sandboxes: int = 200

    @model_validator(mode="after")
    def _mailboxes_are_usable(self) -> Self:
        if self.mailboxes and self.pilot_company_id is None:
            raise ValueError("set PILOT_COMPANY_ID: MAILBOXES are read into that company")
        # A folder's cursor is keyed by the mailbox's username; two accounts sharing one would
        # overwrite each other's.
        usernames = [mailbox.username.lower() for mailbox in self.mailboxes]
        if len(set(usernames)) != len(usernames):
            raise ValueError("each mailbox in MAILBOXES needs its own username")
        return self

    @model_validator(mode="after")
    def _deployed_settings_are_complete(self) -> Self:
        if self.app_env not in ("staging", "production"):
            return self
        # The default suits a laptop; deployed, it would send every invite to localhost.
        if self.web_url == LOCAL_WEB_URL:
            raise ValueError("set WEB_URL to the deployed web app's address")
        required = ("s3_endpoint_url", "s3_bucket", "s3_access_key_id", "s3_secret_access_key")
        missing = [name.upper() for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"set {', '.join(missing)}: file storage is required when deployed")
        if any(not mailbox.tls for mailbox in self.mailboxes):
            raise ValueError("MAILBOXES must use TLS when deployed")
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
