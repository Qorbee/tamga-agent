import uuid

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Mailbox, Settings


def test_a_deployment_without_its_web_address_does_not_start() -> None:
    # Invites would otherwise point at localhost.
    with pytest.raises(ValidationError, match="WEB_URL"):
        Settings(app_env="production")  # type: ignore[call-arg]  # DATABASE_URL: from the env


def test_a_deployment_without_file_storage_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET", raising=False)

    with pytest.raises(ValidationError, match="S3_BUCKET"):
        Settings(app_env="production", web_url="https://tamga.qorbee.com")  # type: ignore[call-arg]  # DATABASE_URL: from the env


def test_a_complete_deployment_starts() -> None:
    # The test environment sets the S3_* variables (conftest.py).
    settings = Settings(app_env="production", web_url="https://tamga.qorbee.com")  # type: ignore[call-arg]  # DATABASE_URL: from the env

    assert settings.web_url == "https://tamga.qorbee.com"


def mailbox(
    *, host: str = "imap.example.com", username: str = "ops@broker.example", tls: bool = True
) -> Mailbox:
    return Mailbox(host=host, username=username, password=SecretStr("x"), tls=tls)


def test_mailboxes_without_a_company_to_read_into_do_not_start() -> None:
    with pytest.raises(ValidationError, match="PILOT_COMPANY_ID"):
        Settings(mailboxes=[mailbox()])  # type: ignore[call-arg]  # DATABASE_URL: from the env


def test_two_mailboxes_with_one_username_do_not_start() -> None:
    with pytest.raises(ValidationError, match="own username"):
        Settings(  # type: ignore[call-arg]  # DATABASE_URL: from the env
            pilot_company_id=uuid.uuid7(),
            mailboxes=[
                mailbox(),
                mailbox(host="imap.other.example", username="OPS@broker.example"),
            ],
        )


def test_a_deployment_never_reads_mail_in_the_clear() -> None:
    with pytest.raises(ValidationError, match="TLS"):
        Settings(  # type: ignore[call-arg]  # DATABASE_URL: from the env
            app_env="production",
            web_url="https://tamga.qorbee.com",
            pilot_company_id=uuid.uuid7(),
            mailboxes=[mailbox(tls=False)],
        )


def test_a_mail_batch_of_zero_does_not_start() -> None:
    with pytest.raises(ValidationError, match="mail_batch"):
        Settings(mail_batch=0)  # type: ignore[call-arg]  # DATABASE_URL: from the env
