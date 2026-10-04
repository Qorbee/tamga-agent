"""A bill that waits for the owner's approval is written about in the chat, with a card the owner
answers on (ADR-0018): in its job's chat or a new one, past the day's chats too, once."""

from collections.abc import Callable

import httpx
import pytest

from app.config import get_settings
from app.modules.assistant import turns
from app.modules.decisions import service as decisions
from app.packs.logistics import sandbox
from app.tests.assistant.test_chats import Script
from app.tests.assistant.test_chats import model as owner_asks  # noqa: F401  (the fixture)
from app.tests.assistant.test_turns import chats_of, shown
from app.tests.assistant.test_turns import tamga as tamga  # noqa: F401  (the fixture)
from app.tests.decisions.test_decisions import Vault, a_sandbox, by_bill
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def test_a_bill_to_approve_is_written_about_with_its_card_past_the_days_chats(
    client: httpx.AsyncClient,
    vault: Vault,
    tamga: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await a_sandbox(client)
    await decisions.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    # No new chat is left for the day, and still the approval is not put off to the digest.
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 0)
    script = tamga("Atlas delivered and the client paid: I would pay it now.")

    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    assert chat["digest_on"] is None and chat["origin"] == "tamga"
    assert sandbox.DELIVERED_BILL in chat["title"]
    messages = await shown(client, chat["id"])
    card = messages[-1]
    assert card["activityType"] == "decision_approval"
    assert card["content"]["id"] == delivered["id"]
    # What the run was told is fenced outside data: the bill, its test USDC and the rules.
    told = str(script.requests[0])
    assert sandbox.DELIVERED_BILL in told and "0.2204 test USDC" in told
    # The held bill waits for no approval, and each bill is written about once.
    assert await turns.speak_pending() == 0
    assert len(await chats_of(client)) == 1
