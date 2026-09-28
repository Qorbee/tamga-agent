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
# The model providers an agent's model may name, and the setting holding each one's key (ADR-0005).
MODEL_PROVIDER_KEYS = {"openai": "openai_api_key", "anthropic": "anthropic_api_key"}


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
    # An empty value (KEY= in .env) counts as unset: otherwise a blank key would pass for a set one,
    # and a blank line in a connection would read as half of it being set.
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", env_ignore_empty=True)

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

    # The pilot's accounting system, the accounting system, read-only into PILOT_COMPANY_ID (ADR-0008): the
    # API key and the admin account it signs in as. Its login answers with every other call's base
    # URL.
    books_api_key: SecretStr | None = None
    books_email: str | None = None
    books_password: SecretStr | None = None
    books_login_url: str = "https://accounting.example.com"
    # Reads a month Tamga allows itself, kept under the accounting system's quota of 3,000 so the owner's own
    # integrations still have room.
    books_monthly_reads: int = Field(default=2500, gt=0)

    # Language models (ADR-0005). A model is "provider:name". Every agent uses DEFAULT_MODEL unless
    # AGENT_MODELS names another for it, so moving one agent to another provider is configuration.
    default_model: str = "openai:gpt-6-sol"
    agent_models: dict[str, str] = {}
    # Only a provider some model names needs its key.
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # TypeSafe's Jev, for classification agents only; unset, they run on a language model.
    typesafe_api_key: SecretStr | None = None

    # Traces of agent runs, to Langfuse Cloud's EU region (ADR-0007). Unset: nothing is exported.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # Circle's developer-controlled wallets on Arc (docs/architecture/05). Testnet only: a live key
    # is refused (root AGENTS.md, invariant 4).
    circle_api_key: SecretStr | None = None
    circle_entity_secret: SecretStr | None = None

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
    def _connections_are_whole(self) -> Self:
        """A half-set connection fails here, at startup, not at its first call."""
        pairs = {
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY": (
                self.langfuse_public_key,
                self.langfuse_secret_key,
            ),
            "CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET": (
                self.circle_api_key,
                self.circle_entity_secret,
            ),
            "BOOKS_API_KEY, BOOKS_EMAIL and BOOKS_PASSWORD": (
                self.books_api_key,
                self.books_email,
                self.books_password,
            ),
        }
        for names, values in pairs.items():
            if any(value is None for value in values) and any(v is not None for v in values):
                raise ValueError(f"set all of {names}, or none")
        if self.books_api_key is not None and self.pilot_company_id is None:
            raise ValueError("set PILOT_COMPANY_ID: the accounting system is read into that company")
        if (
            self.circle_api_key is not None
            and not self.circle_api_key.get_secret_value().startswith("TEST_API_KEY:")
        ):
            raise ValueError("CIRCLE_API_KEY must be a testnet key (TEST_API_KEY:…)")
        for model in self.models():
            provider, _, name = model.partition(":")
            if provider not in MODEL_PROVIDER_KEYS or not name:
                known = ", ".join(MODEL_PROVIDER_KEYS)
                raise ValueError(
                    f"model {model!r} must be provider:name, the provider one of {known}"
                )
        return self

    def models(self) -> set[str]:
        """Every model some agent may use."""
        return {self.default_model, *self.agent_models.values()}

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
        for model in sorted(self.models()):
            key = MODEL_PROVIDER_KEYS[model.partition(":")[0]]
            if getattr(self, key) is None:
                raise ValueError(f"set {key.upper()}: the model {model} needs it")
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
