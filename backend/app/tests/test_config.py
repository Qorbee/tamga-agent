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
    settings = load_settings(
        app_env="production", web_url="https://tamga.qorbee.com", openai_api_key="sk-x"
    )

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


def test_a_model_must_name_a_known_provider() -> None:
    with pytest.raises(ValidationError, match="provider:name"):
        load_settings(agent_models={"linker": "claude-sonnet-5"})


def test_a_deployment_needs_the_key_of_every_provider_its_models_name() -> None:
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        load_settings(
            app_env="production",
            web_url="https://tamga.qorbee.com",
            openai_api_key="sk-x",
            agent_models={"linker": "anthropic:claude-sonnet-5"},
        )


@pytest.mark.parametrize(
    "values",
    [
        {"langfuse_public_key": "pk-lf-x"},
        {"circle_api_key": "TEST_API_KEY:x:y"},
        {"books_api_key": "x", "books_email": "admin@example.com"},
    ],
)
def test_a_half_set_connection_does_not_start(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="or none"):
        load_settings(**values)


def test_the_books_need_a_company_to_read_into() -> None:
    with pytest.raises(ValidationError, match="PILOT_COMPANY_ID"):
        load_settings(
            books_api_key="x",
            books_email="admin@example.com",
            books_password="x",  # noqa: S106 (a test value)
        )


def test_a_live_circle_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="testnet"):
        load_settings(
            circle_api_key="LIVE_API_KEY:x:y",
            circle_entity_secret="x",  # noqa: S106 (a test value)
        )


def test_a_chain_address_that_is_not_one_is_refused() -> None:
    # A typo here would send a deploy, or later a payment, to the wrong contract.
    with pytest.raises(ValidationError, match="policy_vault_address"):
        load_settings(policy_vault_address="0x1234")
    with pytest.raises(ValidationError, match="usdc_address"):
        load_settings(usdc_address="3600000000000000000000000000000000000000")

    settings = load_settings(policy_vault_address="0x" + "aB" * 20)
    assert settings.policy_vault_address == "0x" + "aB" * 20
    assert settings.usdc_address == "0x3600000000000000000000000000000000000000"


def test_a_blank_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("BOOKS_API_KEY", "")

    assert load_settings().openai_api_key is None
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        load_settings(app_env="production", web_url="https://tamga.qorbee.com")
