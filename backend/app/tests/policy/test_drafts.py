"""A change to the policy drafted in a chat holds only once the owner confirms it."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.modules.assistant import tools
from app.modules.assistant.seat import Seat
from app.modules.policy import service
from app.modules.policy.schemas import PolicyChange
from app.platform.auth import Actor
from app.platform.db import open_session
from app.platform.errors import BadRequest
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def drafted(company_id: uuid.UUID, **change: Any) -> str:
    async with open_session() as db:
        draft = await service.draft_change(
            db, company_id, PolicyChange(**change), said="ask me above $2,000", chat_id=None
        )
    return str(draft.id)


async def test_a_draft_changes_nothing_until_the_owner_confirms_it(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    before = (await client.get("/policy")).json()
    draft_id = await drafted(company_id, approval_limit=Decimal(2000), currency="USD")

    assert (await client.get("/policy")).json() == before
    shown = (await client.get(f"/policy/drafts/{draft_id}")).json()
    assert shown["state"] == "draft"
    assert shown["change"]["approval_limit"] == "2000"

    confirmed = (await client.post(f"/policy/drafts/{draft_id}/confirm")).json()
    after = (await client.get("/policy")).json()
    assert confirmed["state"] == "confirmed"
    assert confirmed["made_version"] == after["version"] == before["version"] + 1
    assert Decimal(after["approval_limit"]) == 2000
    # What the draft did not speak of is kept.
    assert after["pay_when_paid"] == before["pay_when_paid"]
    # A second tap never makes a second version.
    assert (await client.post(f"/policy/drafts/{draft_id}/confirm")).status_code == 409
    assert (await client.get("/policy")).json()["version"] == after["version"]


async def test_a_discarded_draft_leaves_the_policy_as_it_was(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    before = (await client.get("/policy")).json()
    draft_id = await drafted(company_id, pay_when_paid=not before["pay_when_paid"])

    discarded = (await client.post(f"/policy/drafts/{draft_id}/discard")).json()

    assert discarded["state"] == "discarded"
    assert (await client.get("/policy")).json() == before
    assert (await client.post(f"/policy/drafts/{draft_id}/confirm")).status_code == 409


async def test_a_draft_that_changes_nothing_is_refused(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    policy = (await client.get("/policy")).json()

    with pytest.raises(BadRequest):
        await drafted(company_id, pay_when_paid=policy["pay_when_paid"])


async def test_the_chat_drafts_the_owners_words_and_names_what_it_cannot_read(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    seat = Seat(
        company_id=company_id,
        thread_id=None,
        actor=Actor.of_tamga(company_id),
        chat_id=uuid.uuid7(),
    )
    context: Any = SimpleNamespace(deps=seat)

    made = await tools.draft_policy(
        context, "pay on Fridays, keep two weeks covered", pay_day="friday", reserve="steady"
    )
    assert isinstance(made, dict)
    change = (await client.get(f"/policy/drafts/{made['draft_id']}")).json()["change"]
    assert change["payment_weekday"] == 4
    assert change["reserve_rule"] == "steady"

    assert "currency" in str(
        await tools.draft_policy(context, "ask me above 2000", approval_limit=Decimal(2000))
    )
    assert "No reserve rule" in str(await tools.draft_policy(context, "keep a lot", reserve="lots"))
