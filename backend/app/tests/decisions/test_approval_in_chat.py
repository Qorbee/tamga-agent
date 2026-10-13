"""The bills that wait for the owner's approval are written about in the chat, together, with one
card the owner answers on (ADR-0018): in their job's chat or a new one, past the day's chats too,
once."""

import uuid
from collections.abc import Callable

import httpx
import pytest

from app.config import get_settings
from app.modules.assistant import turns
from app.modules.decisions import service as decisions
from app.packs.logistics import sandbox
from app.platform.db import open_session
from app.tests.assistant.test_chats import Script
from app.tests.assistant.test_chats import model as owner_asks  # noqa: F401  (the fixture)
from app.tests.assistant.test_turns import cards_in, chats_of, shown
from app.tests.assistant.test_turns import tamga as tamga  # noqa: F401  (the fixture)
from app.tests.decisions.test_decisions import Vault, a_sandbox, by_bill
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


@pytest.fixture(autouse=True)
def welcomed(monkeypatch: pytest.MonkeyPatch) -> None:
    """These sandboxes stand in for any company: their welcome is left out, and tested with the
    scenarios (tests/logistics/test_scenarios.py)."""

    async def none(db: object, company_id: object) -> list[turns.Finding]:
        return []

    monkeypatch.setattr(turns, "_opening", none)


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
    # No controller: nothing to wait for.
    monkeypatch.setattr(get_settings(), "advice_per_day", 0)
    script = tamga("Atlas delivered and the client paid: I would pay it now.")

    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    assert chat["digest_on"] is None and chat["origin"] == "tamga"
    assert sandbox.DELIVERED_BILL in chat["title"]
    messages = await shown(client, chat["id"])
    card = messages[-1]
    assert card["activityType"] == "decision_batch"
    assert card["content"]["id"] == delivered["id"]
    assert card["content"]["ids"] == [delivered["id"]]
    assert card["content"]["evaluations"] == [delivered["evaluation"]]
    # What the run was told is fenced outside data: the bill, its test USDC and the rules.
    told = str(script.requests[0])
    assert sandbox.DELIVERED_BILL in told and "0.2204 test USDC" in told
    # The held bill waits for no approval, and each bill is written about once.
    assert await turns.speak_pending() == 0
    assert len(await chats_of(client)) == 1


async def test_the_message_waits_for_the_controllers_opinion_and_tells_it(
    client: httpx.AsyncClient,
    vault: Vault,
    tamga: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_sandbox(client)
    await decisions.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    monkeypatch.setattr(get_settings(), "advice_per_day", 5)
    script = tamga("Atlas delivered, but it is due in a month: I would wait.")

    # The controller weighs it within minutes: the message waits for its opinion.
    assert await turns.speak_pending() == 0 and script.requests == []

    async with open_session() as db:
        assert await decisions.keep_advice(
            db,
            company_id,
            uuid.UUID(delivered["id"]),
            1,
            {
                "evaluation": 1,
                "proposal": "hold",
                "amount": None,
                "reason": "Due in a month, and the client has not paid.",
                "model": "test",
            },
        )
    assert await turns.speak_pending() == 1
    told = str(script.requests[0])
    assert "The controller proposes: hold. Its reason: Due in a month" in told


async def test_with_the_days_budget_spent_the_message_does_not_wait(
    client: httpx.AsyncClient,
    vault: Vault,
    tamga: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_sandbox(client)
    await decisions.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    monkeypatch.setattr(get_settings(), "advice_per_day", 1)
    async with open_session() as db:
        await decisions.ask_advice(db, company_id, uuid.UUID(delivered["id"]), 1, "0" * 64)
    tamga("Atlas delivered and the client paid: I would pay it now.")

    assert await turns.speak_pending() == 1


async def test_the_bills_that_wait_together_are_one_message_and_one_card(
    client: httpx.AsyncClient,
    vault: Vault,
    tamga: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await a_sandbox(client, scenario="payday")
    await decisions.evaluate_pending()
    waiting = (await client.get("/decisions", params={"state": "awaiting_owner"})).json()["items"]
    assert len(waiting) > 1
    monkeypatch.setattr(get_settings(), "advice_per_day", 0)
    script = tamga("Two can be paid now; Orbit would go under the reserve, so I would hold it.")

    assert await turns.speak_pending() == 1

    # One run, one chat, one card holding every bill, each written about once.
    assert len(script.requests) == 1
    [chat] = await chats_of(client)
    assert chat["title"] == f"{len(waiting)} bills to approve"
    cards = await cards_in(client, chat["id"])
    assert [card["activityType"] for card in cards] == ["decision_batch"]
    assert set(cards[0]["content"]["ids"]) == {decision["id"] for decision in waiting}
    told = str(script.requests[0])
    assert "Bill 1:" in told and f"Bill {len(waiting)}:" in told
    assert "never say it was breached" in told
    assert await turns.speak_pending() == 0

    # The batch's own records are filtered by id.
    listed = await client.get(
        "/decisions", params={"id": [decision["id"] for decision in waiting[:2]]}
    )
    assert {item["id"] for item in listed.json()["items"]} == {d["id"] for d in waiting[:2]}


async def test_a_bill_answered_while_the_model_wrote_stays_on_the_card_as_answered(
    client: httpx.AsyncClient,
    vault: Vault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_sandbox(client, scenario="payday")
    await decisions.evaluate_pending()
    monkeypatch.setattr(get_settings(), "advice_per_day", 0)
    async with open_session() as db:
        [finding] = await turns._approvals(db, company_id)
    ids = finding.card["ids"]
    assert len(ids) > 1

    async def hold(decision_id: str) -> None:
        shown = (await client.get(f"/decisions/{decision_id}")).json()
        held = await client.patch(
            f"/decisions/{decision_id}", json={"action": "hold", "evaluation": shown["evaluation"]}
        )
        assert held.status_code == 200, held.text

    # One answered: the message still speaks of the rest, and the card keeps every bill, the
    # answered one shown as answered.
    await hold(ids[0])
    async with open_session() as db:
        assert await turns._stands(db, company_id, finding)
    assert finding.card["ids"] == ids
    # All answered: nothing is left to ask, and nothing is kept.
    for decision_id in ids[1:]:
        await hold(decision_id)
    async with open_session() as db:
        assert not await turns._stands(db, company_id, finding)


async def test_a_long_backlog_is_written_about_fifty_bills_at_a_time(
    client: httpx.AsyncClient,
    vault: Vault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_sandbox(client, scenario="payday")
    await decisions.evaluate_pending()
    monkeypatch.setattr(get_settings(), "advice_per_day", 0)
    monkeypatch.setattr(turns, "BATCH", 2)
    async with open_session() as db:
        findings = await turns._approvals(db, company_id)
    sizes = [len(finding.card["ids"]) for finding in findings]
    assert all(size <= 2 for size in sizes) and sum(sizes) > 2
