from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.platform import turnstile


@pytest.fixture
def cloudflare(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """The widget's secret set, and every request siteverify is sent."""
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", SecretStr("0x4AAA-secret"))
    monkeypatch.setattr(get_settings(), "turnstile_hostnames", "tamga.example.com")
    return []


def answers(
    monkeypatch: pytest.MonkeyPatch,
    seen: list[httpx.Request],
    answer: dict[str, Any],
    status: int = 200,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer)

    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handle), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)


PASSED = {"success": True, "action": "guest", "hostname": "tamga.example.com"}


async def test_without_a_secret_nothing_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", None)
    seen: list[httpx.Request] = []
    answers(monkeypatch, seen, PASSED)

    assert await turnstile.verify(None, action="guest", client="203.0.113.7") is True
    assert seen == []


async def test_a_token_for_the_action_on_the_web_app_passes(
    monkeypatch: pytest.MonkeyPatch, cloudflare: list[httpx.Request]
) -> None:
    answers(monkeypatch, cloudflare, PASSED)

    assert await turnstile.verify("token", action="guest", client="203.0.113.7") is True
    sent = cloudflare[0].content.decode()
    assert "secret=0x4AAA-secret" in sent
    assert "response=token" in sent
    assert "remoteip=203.0.113.7" in sent


@pytest.mark.parametrize(
    "answer",
    [
        {**PASSED, "success": False, "error-codes": ["timeout-or-duplicate"]},
        {**PASSED, "action": "login"},
        {**PASSED, "hostname": "elsewhere.example.com"},
        {"success": True},
    ],
)
async def test_a_token_for_anything_else_fails(
    monkeypatch: pytest.MonkeyPatch, cloudflare: list[httpx.Request], answer: dict[str, Any]
) -> None:
    answers(monkeypatch, cloudflare, answer)

    assert await turnstile.verify("token", action="guest", client="203.0.113.7") is False


async def test_cloudflare_not_answering_fails_closed(
    monkeypatch: pytest.MonkeyPatch, cloudflare: list[httpx.Request]
) -> None:
    answers(monkeypatch, cloudflare, {}, status=502)

    assert await turnstile.verify("token", action="guest", client="203.0.113.7") is False


async def test_no_token_fails_without_asking(
    monkeypatch: pytest.MonkeyPatch, cloudflare: list[httpx.Request]
) -> None:
    answers(monkeypatch, cloudflare, PASSED)

    assert await turnstile.verify(None, action="guest", client="203.0.113.7") is False
    assert await turnstile.verify("x" * 2049, action="guest", client="203.0.113.7") is False
    assert cloudflare == []
