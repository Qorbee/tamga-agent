"""Cloudflare Turnstile: whether a browser that asks for something costly is a person's.

The widget in the page hands the browser a token; the token is redeemed here, once, at
Cloudflare's siteverify. A token is accepted only for the action it was issued for and on one of
the web app's own hostnames. Anything else fails closed, Cloudflare not answering included.
"""

import logging
from typing import Final

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_SITEVERIFY: Final = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT: Final = 10.0
# Cloudflare's tokens are at most 2048 characters.
_LONGEST: Final = 2048


def required() -> bool:
    """Whether a token is asked for here: only once the widget's secret is set."""
    return get_settings().turnstile_secret_key is not None


async def verify(token: str | None, *, action: str, client: str) -> bool:
    """Whether ``token`` proves a person in a browser on the web app asked for ``action``. True
    where no secret is set."""
    settings = get_settings()
    secret = settings.turnstile_secret_key
    if secret is None:
        return True
    if not token or len(token) > _LONGEST:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            response = await http.post(
                _SITEVERIFY,
                data={"secret": secret.get_secret_value(), "response": token, "remoteip": client},
            )
            response.raise_for_status()
            answer = response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Turnstile could not be asked: %s", type(error).__name__)
        return False
    if not isinstance(answer, dict):
        return False
    passed = (
        answer.get("success") is True
        and answer.get("action") == action
        and answer.get("hostname") in settings.turnstile_hostname_set
    )
    if not passed:
        # Cloudflare's codes say why ("timeout-or-duplicate"), and hold nothing about the person.
        logger.info("a Turnstile token was refused: %s", answer.get("error-codes"))
    return passed
