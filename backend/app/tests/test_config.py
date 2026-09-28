import pytest
from pydantic import ValidationError

from app.config import Settings


def test_a_deployment_without_its_web_address_does_not_start() -> None:
    # Invites would otherwise point at localhost.
    with pytest.raises(ValidationError, match="WEB_URL"):
        Settings(app_env="production")  # type: ignore[call-arg]  # DATABASE_URL: from the env


def test_a_deployment_with_its_web_address_starts() -> None:
    settings = Settings(app_env="production", web_url="https://tamga.qorbee.com")  # type: ignore[call-arg]  # DATABASE_URL: from the env

    assert settings.web_url == "https://tamga.qorbee.com"
