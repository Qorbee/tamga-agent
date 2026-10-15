"""A payment rule of the company's own, from the chat's words to the policy in force: groups of
firms named once, a rule drafted on them, held only once the owner confirms it."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.modules.assistant import tools
from app.modules.assistant.seat import Seat
from app.modules.playground import service as playground
from app.platform.auth import Actor, Role
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def test_groups_are_named_once_and_hold_the_companys_own_firms(
    client: httpx.AsyncClient,
) -> None:
    await playground.seed(await guest_client(client, "payday"))
    firms = (await client.get("/parties", params={"limit": 2})).json()["items"]
    first, second = (firm["id"] for firm in firms)

    made = await client.post("/party-groups", json={"name": "Needs cash", "party_ids": [first]})
    assert made.status_code == 201
    # A name is the company's once, whatever its case.
    taken = await client.post("/party-groups", json={"name": "needs CASH", "party_ids": []})
    assert taken.status_code == 409
    group = made.json()["id"]
    saved = await client.put(
        f"/party-groups/{group}", json={"name": "Needs cash", "party_ids": [first, second]}
    )
    assert saved.json()["party_ids"] == [first, second]
    # One firm out and back in, the other left as it is.
    out = await client.delete(f"/party-groups/{group}/members/{first}")
    assert out.json()["party_ids"] == [second]
    back = await client.put(f"/party-groups/{group}/members/{first}")
    assert back.json()["party_ids"] == [second, first]
    elsewhere = await client.post(
        "/party-groups", json={"name": "Strangers", "party_ids": [str(uuid.uuid4())]}
    )
    assert elsewhere.status_code == 404
    listed = (await client.get("/party-groups")).json()
    assert [g["name"] for g in listed["items"]] == ["Needs cash"] and listed["total"] == 1
    assert (await client.delete(f"/party-groups/{group}")).status_code == 204
    assert (await client.get("/party-groups")).json()["items"] == []


async def test_the_chat_drafts_a_rule_that_holds_once_confirmed(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client, "payday")
    await playground.seed(company_id)
    firms = (await client.get("/parties", params={"limit": 1})).json()["items"]
    seat = Seat(
        company_id=company_id,
        thread_id=None,
        # The owner chatting: Tamga's own turns group no firms.
        actor=Actor(
            user_id=uuid.uuid4(), company_id=company_id, role=Role.OWNER, session_id=uuid.uuid4()
        ),
        chat_id=uuid.uuid7(),
    )
    seat.handles["p"] = {1: uuid.UUID(firms[0]["id"])}
    context: Any = SimpleNamespace(deps=seat)
    cash = tools.When(attribute="group", operator="in", value=["needs cash"])

    assert "No group" in str(await tools.draft_rule(context, "cash first", [cash], "first"))
    assert "holds 1 firms" in await tools.group_firms(context, "Needs cash", ["p1"])
    made = await tools.draft_rule(context, "pay those who need cash first", [cash], "first")
    assert isinstance(made, dict)
    change = (await client.get(f"/policy/drafts/{made['draft_id']}")).json()["change"]
    rule = change["save_custom"]
    assert rule["then"] == "first" and rule["when"][0]["attribute"] == "group"
    # Nothing holds until the owner confirms it; what it applies to is shown, nothing moved.
    assert (await client.get("/policy")).json()["custom_rules"] == []
    assert (await client.post("/decisions/matching", json=rule)).status_code == 200
    await client.post(f"/policy/drafts/{made['draft_id']}/confirm")
    assert [r["key"] for r in (await client.get("/policy")).json()["custom_rules"]] == [rule["key"]]
    # Once a rule names the group, who is in it is the owner's to change on a firm's page.
    assert "names Needs cash" in await tools.group_firms(context, "needs cash", ["p1"])
    # A condition outside the catalogue is refused before anything is drafted.
    wrong = tools.When(attribute="days_owed", operator="in", value=30)
    assert "Not drafted" in str(await tools.draft_rule(context, "old ones", [wrong], "ask"))
