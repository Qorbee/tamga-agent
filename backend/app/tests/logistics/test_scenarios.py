import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant import turns
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.conversations import service as conversations
from app.modules.playground import service as playground
from app.modules.playground.schemas import Choices
from app.packs.logistics import sandbox
from app.platform.auth import Actor, Role
from app.platform.db import open_session
from app.platform.errors import NotFound
from app.tests.assistant.test_chats import Script
from app.tests.assistant.test_chats import model as owner_asks  # noqa: F401  (the fixture)
from app.tests.assistant.test_turns import chats_of, shown
from app.tests.assistant.test_turns import tamga as tamga  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def opened(client: httpx.AsyncClient, scenario: str | None = None) -> uuid.UUID:
    body = {"scenario": scenario} if scenario else None
    response = await client.post("/sessions/guest", json=body)
    assert response.status_code == 201, response.text
    company_id = uuid.UUID(response.json()["company"]["id"])
    await playground.seed(company_id)
    return company_id


async def simulated(client: httpx.AsyncClient, *, earlier: bool = False) -> list[dict[str, object]]:
    """The world's messages; the ones from before the sandbox opened only with ``earlier``."""
    items: list[dict[str, object]] = (
        await client.get("/messages", params={"channel": "simulated"})
    ).json()["items"]
    return [
        item for item in items if earlier or not str(item["rfc_message_id"]).startswith("<earlier-")
    ]


async def test_the_scenarios_are_listed_before_anyone_signs_in(client: httpx.AsyncClient) -> None:
    listed = (await client.get("/playground/scenarios")).json()
    assert [scenario["key"] for scenario in listed] == ["freight", "payday", "week"]
    assert all(scenario["title"] and scenario["summary"] for scenario in listed)


async def test_an_unknown_scenario_opens_no_sandbox(client: httpx.AsyncClient) -> None:
    response = await client.post("/sessions/guest", json={"scenario": "nope"})
    assert response.status_code == 400
    async with open_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM playground_worlds")) == 0


async def test_short_cash_opens_with_three_carriers_owed_and_a_reserve(
    client: httpx.AsyncClient,
) -> None:
    await opened(client, "payday")

    world = (await client.get("/playground/world")).json()
    assert world["scenario"]["key"] == "payday" and world["days_skipped"] == 0
    assert [persona["key"] for persona in world["personas"]] == ["petr", "marta", "grace", "jeroen"]
    assert [persona["medium"] for persona in world["personas"]] == [
        "whatsapp",
        "whatsapp",
        "mail",
        "mail",
    ]
    # Its first steps play as it opens: the fragile carrier and Kestrel ask to be paid.
    assert world["played"] == 2
    said = {str(message["thread_key"]): message for message in await simulated(client)}
    assert sandbox.RUN_BILLS[2] in str(said[sandbox.CARRIER.thread_key]["snippet"])
    assert sandbox.RUN_BILLS[1] in str(said[sandbox.DISPATCHER.thread_key]["snippet"])

    policy = (await client.get("/policy")).json()
    assert policy["reserve"]["key"] == "steady" and policy["currency"] == "EUR"
    assert policy["payment_weekday"] == 4
    projects = (await client.get("/projects")).json()["items"]
    assert {project["number"] for project in projects} == {sandbox.RUN_SALE, sandbox.NEXT_SALE}
    accounts = (await client.get("/money/accounts")).json()["items"]
    assert sorted(Decimal(account["balance"]) for account in accounts) == [900, 4200]


async def test_skipping_a_day_moves_the_clock_and_plays_the_script(
    client: httpx.AsyncClient,
) -> None:
    company_id = await opened(client)
    before = date.fromisoformat((await client.get("/me")).json()["today"])
    # The driver and the client's desk write as it opens.
    assert len(await simulated(client)) == 2

    world = (await client.post("/playground/world/skip", json={"days": 1})).json()
    assert world["days_skipped"] == 1
    assert date.fromisoformat((await client.get("/me")).json()["today"]) == before + timedelta(
        days=1
    )
    # The skip queued the play; the worker runs it.
    assert await playground.play_due(company_id) == 1

    [delivered] = [
        message
        for message in await simulated(client)
        if message["sender"] == sandbox.DISPATCHER.address
    ]
    assert sandbox.PLATE_MOVING in str(delivered["snippet"])
    assert delivered["attachment_count"] == 1
    # Read by the same intake as any mail: it waits to be processed like one, with what was
    # said before the sandbox opened.
    assert await conversations.process_pending() == 3 + len(sandbox.FREIGHT_EARLIER)
    # Played once: a second pass on the same day plays nothing.
    assert await playground.play_due(company_id) == 0


async def test_the_judge_types_as_a_persona(client: httpx.AsyncClient) -> None:
    await opened(client)

    said = await client.post(
        "/playground/world/messages",
        json={"persona": sandbox.DISPATCHER.key, "text": "Truck is stuck at the border."},
    )
    assert said.status_code == 204
    [message] = [
        item for item in await simulated(client) if item["sender"] == sandbox.DISPATCHER.address
    ]
    assert message["sender"] == sandbox.DISPATCHER.address
    assert message["snippet"] == "Truck is stuck at the border."

    nobody = await client.post(
        "/playground/world/messages", json={"persona": "someone", "text": "Hello"}
    )
    assert nobody.status_code == 404


async def test_a_pilot_has_no_world_and_its_clock_never_moves(db: AsyncSession) -> None:
    pilot = await companies.create_company(db, name="Pilot", kind=CompanyKind.PILOT)
    await db.commit()
    owner = Actor(
        user_id=uuid.uuid4(), company_id=pilot.id, role=Role.OWNER, session_id=uuid.uuid4()
    )

    with pytest.raises(NotFound):
        await playground.skip(db, owner, 1)
    await companies.advance_clock(db, pilot.id, timedelta(days=1))
    await db.commit()
    await db.refresh(pilot)
    assert pilot.clock_offset == timedelta(0)


async def test_two_days_on_the_client_pays_and_the_bank_holds_it(
    client: httpx.AsyncClient,
) -> None:
    company_id = await opened(client, "payday")

    await client.post("/playground/world/skip", json={"days": 2})
    assert await playground.play_due(company_id) == 4

    bills = {bill["number"]: bill for bill in (await client.get("/invoices")).json()["items"]}
    assert Decimal(bills[sandbox.NEXT_SALE]["remaining_amount"]) == 0
    accounts = (await client.get("/money/accounts")).json()["items"]
    assert sorted(Decimal(account["balance"]) for account in accounts) == [900, 10700]
    paid = [
        item
        for item in (await client.get("/money/movements")).json()["items"]
        if item["reference"] == sandbox.NEXT_SALE
    ]
    assert [Decimal(item["amount"]) for item in paid] == [sandbox.NEXT_PAYMENT]
    # Petr twice, Marta, Atlas's reminder and the remittance advice.
    assert len(await simulated(client)) == 5


async def test_a_skip_waits_for_the_sandbox_to_open(client: httpx.AsyncClient) -> None:
    await client.post("/sessions/guest", json={"scenario": "freight"})
    # Seeding is queued, not run: the script would count from the wrong day.
    assert (await client.get("/playground/world")).json()["ready"] is False
    assert (await client.post("/playground/world/skip", json={"days": 1})).status_code == 409


async def test_a_step_played_again_stores_its_message_once(client: httpx.AsyncClient) -> None:
    company_id = await opened(client)
    # A worker that died after the step and before marking it played plays it again.
    await sandbox._moving_asks_to_be_paid(company_id)
    await sandbox._moving_asks_to_be_paid(company_id)
    assert len(await simulated(client)) == 3


async def test_tamga_writes_first_when_a_sandbox_opens(
    client: httpx.AsyncClient,
    tamga: Callable[..., Script],  # noqa: F811
) -> None:
    await opened(client, "payday")
    script = tamga("Three carriers are owed this week; I would look at the run first.")

    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    assert chat["origin"] == "tamga" and chat["title"] == "Short cash on Friday"
    card = (await shown(client, chat["id"]))[-1]
    project = next(
        item
        for item in (await client.get("/projects")).json()["items"]
        if item["number"] == sandbox.RUN_SALE
    )
    assert card["content"]["id"] == project["id"]
    assert "Short cash on Friday" in str(script.requests[0])
    # Once.
    assert await turns.speak_pending() == 0


async def test_a_week_of_three_clients_plays_every_step_on_both_channels(
    client: httpx.AsyncClient,
) -> None:
    company_id = await opened(client, "week")
    projects = (await client.get("/projects")).json()["items"]
    assert {project["number"] for project in projects} == set(sandbox.WEEK_SALES)

    for _ in range(4):
        await client.post("/playground/world/skip", json={"days": 1})
        await playground.play_due(company_id)
    world = (await client.get("/playground/world")).json()
    assert world["played"] == world["steps"] == 7

    said = await simulated(client)
    by_sender = {str(message["sender"]): message for message in said}
    # A mail persona writes under its subject; a WhatsApp one as a chat.
    assert by_sender[sandbox.MARCO.address]["subject"] == sandbox.MARCO.subject
    assert str(by_sender[sandbox.PAVEL.address]["subject"]).startswith("WhatsApp")
    assert by_sender[sandbox.PAVEL.address]["attachment_count"] == 1
    assert by_sender[sandbox.ESZTER.address]["attachment_count"] == 1
    bills = {bill["number"]: bill for bill in (await client.get("/invoices")).json()["items"]}
    assert Decimal(bills[sandbox.WEEK_SALES[2]]["remaining_amount"]) == 0


async def test_what_the_judge_types_is_as_long_as_its_channel_allows(
    client: httpx.AsyncClient,
) -> None:
    await opened(client)
    chat_line = {"persona": sandbox.DRIVER.key, "text": "x" * 201}
    assert (await client.post("/playground/world/messages", json=chat_line)).status_code == 400
    mail = {"persona": sandbox.CLIENT_DESK.key, "text": "Dear team,\n" + "x" * 900}
    assert (await client.post("/playground/world/messages", json=mail)).status_code == 204


async def test_a_judge_names_themselves_their_company_and_how_it_pays(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/sessions/guest",
        json={
            "scenario": "payday",
            "name": "Ada Byron",
            "company": "Ada Freight, Inc.",
            "choices": {"approval_limit": "500.00", "reserve_rule": "light", "payment_weekday": 2},
        },
    )
    assert response.status_code == 201, response.text
    me = response.json()
    assert me["user"]["name"] == "Ada Byron" and me["company"]["name"] == "Ada Freight, Inc."
    # The choices ride with the seeding job, which writes them before the scenario's own policy.
    async with open_session() as db:
        args = await db.scalar(
            text("SELECT args FROM procrastinate_jobs WHERE task_name = 'playground.seed'")
        )
    assert args["choices"] == {
        "approval_limit": "500.00",
        "reserve_rule": "light",
        "payment_weekday": 2,
    }
    await playground.seed(uuid.UUID(me["company"]["id"]), Choices.model_validate(args["choices"]))

    policy = (await client.get("/policy")).json()
    assert Decimal(policy["approval_limit"]) == 500 and policy["reserve"]["key"] == "light"
    assert policy["payment_weekday"] == 2 and policy["currency"] == "EUR"
    # A comma in the name is part of it, not a second sender: what the company said is its own.
    sent = [m for m in await simulated(client, earlier=True) if m["direction"] == "outbound"]
    assert len(sent) == sum(earlier.ours for earlier in sandbox.PAYDAY_EARLIER)


async def test_a_sandbox_named_by_nobody_takes_the_scenarios_names(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/sessions/guest", json={"scenario": "freight", "name": "  "})
    me = response.json()
    assert me["user"]["name"] == sandbox.OWNER and me["company"]["name"] == sandbox.COMPANY
    listed = {
        scenario["key"]: scenario for scenario in (await client.get("/playground/scenarios")).json()
    }
    assert listed["payday"]["policy"]["reserve_rule"] == "steady"
    assert listed["freight"]["company"] == sandbox.COMPANY


async def test_payment_rules_that_cannot_hold_open_no_sandbox(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/sessions/guest", json={"scenario": "payday", "choices": {"reserve_rule": "nope"}}
    )
    assert response.status_code == 400
    named = await client.post(
        "/sessions/guest", json={"scenario": "payday", "company": "Line\nbreak"}
    )
    assert named.status_code == 422
    async with open_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM playground_worlds")) == 0


async def test_a_sandbox_opens_on_conversations_already_under_way(
    client: httpx.AsyncClient,
) -> None:
    await opened(client, "freight")

    lena = [
        message
        for message in await simulated(client, earlier=True)
        if message["thread_key"] == sandbox.CLIENT_DESK.thread_key
    ]
    directions = sorted((str(message["sent_at"]), message["direction"]) for message in lena)
    # The client's order, then the company's answer, both before the sandbox's day.
    assert [direction for _, direction in directions[:2]] == ["inbound", "outbound"]
    ours = next(message for message in lena if message["direction"] == "outbound")
    assert sandbox.COMPANY in str(ours["snippet"]) or "{company}" not in str(ours["snippet"])
    # Dated in the order written, apart, so a reply never shows above what it answers.
    earlier = sorted(
        (str(message["sent_at"]), str(message["rfc_message_id"]))
        for message in await simulated(client, earlier=True)
        if str(message["rfc_message_id"]).startswith("<earlier-")
    )
    assert len({at for at, _ in earlier}) == len(sandbox.FREIGHT_EARLIER)
    assert [int(rfc.split("-")[1].split(".")[0]) for _, rfc in earlier] == list(
        range(len(sandbox.FREIGHT_EARLIER))
    )
    world = (await client.get("/playground/world")).json()
    assert {persona["key"]: persona["relation"] for persona in world["personas"]}[
        "lena"
    ] == "client"
    # Seeding again stores none of it twice.
    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])
    before = len(await simulated(client, earlier=True))
    await playground.seed(company_id)
    assert len(await simulated(client, earlier=True)) == before
