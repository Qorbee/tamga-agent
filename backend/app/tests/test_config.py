import uuid
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Mailbox, Settings


def load_settings(**values: Any) -> Settings:
    """Settings from the environment conftest.py sets and these values, never the developer's own
    backend/.env: a value set there would decide what these tests see."""
    # _env_file is BaseSettings' own argument, which the pydantic mypy plugin leaves out.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_a_deployment_without_its_web_address_does_not_start() -> None:
    # Invites would otherwise point at localhost.
    with pytest.raises(ValidationError, match="WEB_URL"):
        load_settings(app_env="production")


def test_a_deployment_without_file_storage_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET", raising=False)

    with pytest.raises(ValidationError, match="S3_BUCKET"):
        load_settings(app_env="production", web_url="https://tamga.qorbee.com")


def test_a_complete_deployment_starts() -> None:
    # The test environment sets the S3_* variables (conftest.py).
    settings = load_settings(app_env="production", web_url="https://tamga.qorbee.com")

    assert settings.web_url == "https://tamga.qorbee.com"


def mailbox(
    *, host: str = "imap.example.com", username: str = "ops@broker.example", tls: bool = True
) -> Mailbox:
    return Mailbox(host=host, username=username, password=SecretStr("x"), tls=tls)


def test_mailboxes_without_a_company_to_read_into_do_not_start() -> None:
    with pytest.raises(ValidationError, match="PILOT_COMPANY_ID"):
        load_settings(mailboxes=[mailbox()])


def test_two_mailboxes_with_one_username_do_not_start() -> None:
    with pytest.raises(ValidationError, match="own username"):
        load_settings(
            pilot_company_id=uuid.uuid7(),
            mailboxes=[
                mailbox(),
                mailbox(host="imap.other.example", username="OPS@broker.example"),
            ],
        )


def test_a_deployment_never_reads_mail_in_the_clear() -> None:
    with pytest.raises(ValidationError, match="TLS"):
        load_settings(
            app_env="production",
            web_url="https://tamga.qorbee.com",
            pilot_company_id=uuid.uuid7(),
            mailboxes=[mailbox(tls=False)],
        )


def test_a_mail_batch_of_zero_does_not_start() -> None:
    with pytest.raises(ValidationError, match="mail_batch"):
        load_settings(mail_batch=0)
