"""A project's firms from what names them (docs/architecture/09): two kinds of signal that agree
make a firm the project's until the owner answers; one alone is asked of the owner."""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.conversations.models import ThreadCategory
from app.modules.linking import firms, service
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind, PartyKind
from app.modules.parties.service import Identifier
from app.modules.projects import service as projects
from app.modules.projects.models import ProjectRole
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import (
    Script,
    answer,
    arrive,
    mail,
    model,  # noqa: F401  (the fixture)
)

pytestmark = pytest.mark.integration

ADA = Identifier(IdentifierKind.EMAIL, "ada@stone.example")
TAX = Identifier(IdentifierKind.TAX_ID, "1234567890")
CLIENT_READ = answer("new_project", new_project={"name": "coils", "client": "Stone GmbH"})


async def card(company_id: uuid.UUID, name: str, *identifiers: Identifier) -> uuid.UUID:
    """A customer card in the books."""
    async with open_session() as db:
        party = await parties.sync_party(
            db,
            company_id=company_id,
            source="books",
            source_id=f"{name}-{len(identifiers)}",
            name=name,
            kind=PartyKind.ORGANISATION,
            identifiers=identifiers,
        )
        await db.commit()
        return party.id


async def placed(
    client: httpx.AsyncClient, scripted: Callable[..., Script], company_id: uuid.UUID, raw: bytes
) -> str:
    """A conversation the linker proposed a project for, its client read as Stone GmbH."""
    await arrive(company_id, 1, raw, category=ThreadCategory.WORK)
    scripted(CLIENT_READ)
    assert await service.place_pending() == 1
    [project] = (await client.get("/projects")).json()["items"]
    project_id: str = project["id"]
    return project_id


async def on(client: httpx.AsyncClient, project_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (
        await client.get("/project-parties", params={"project_id": project_id})
    ).json()
    return found


async def suggested_again(company_id: uuid.UUID) -> None:
    async with open_session() as db:
        await firms.suggest_firms(db, company_id)
        await db.commit()


async def test_the_firm_a_reading_and_its_senders_address_name_is_the_projects_client(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    stone = await card(company_id, "STONE GMBH", ADA)
    project_id = await placed(client, model, company_id, mail("<order@x>"))

    [party] = await on(client, project_id)

    assert (party["party_id"], party["role"], party["state"]) == (str(stone), "client", "suggested")
    assert party["signals"] == ["reading", "sender"]
    assert {item["item_kind"] for item in party["evidence"]} == {"message"}
    # Two signals agree: it is the project's client for what rests on it, until the owner says.
    async with open_session() as db:
        held = await projects.firms_of(db, company_id, [uuid.UUID(project_id)])
    assert held[uuid.UUID(project_id)].client.party_ids == {stone}

    answered = await client.patch(f"/project-parties/{party['id']}", json={"state": "confirmed"})
    assert answered.status_code == 200 and answered.json()["state"] == "confirmed"
    # Answered once, it is the owner's: asked again, it is refused.
    again = await client.patch(f"/project-parties/{party['id']}", json={"state": "rejected"})
    assert again.status_code == 409


async def test_a_firm_one_signal_names_is_asked_and_a_no_keeps_it_away(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    stone = await card(company_id, "STONE GMBH")
    # The mail passes another's on: its sender says nothing of the firm.
    project_id = await placed(
        client, model, company_id, mail("<order@x>", subject="Fwd: Munich load")
    )

    [party] = await on(client, project_id)

    assert (party["party_id"], party["signals"]) == (str(stone), ["reading"])
    async with open_session() as db:
        held = await projects.firms_of(db, company_id, [uuid.UUID(project_id)])
    # One signal is a question, never the project's firm.
    assert held[uuid.UUID(project_id)].client.party_ids == frozenset()

    refused = await client.patch(f"/project-parties/{party['id']}", json={"state": "rejected"})
    assert (refused.status_code, refused.json()["state"]) == (200, "rejected")
    # Refused, the firm is not the project's by the name the reading gives either.
    async with open_session() as db:
        held = await projects.firms_of(db, company_id, [uuid.UUID(project_id)])
    assert held[uuid.UUID(project_id)].client.names == frozenset()
    await suggested_again(company_id)
    assert await on(client, project_id) == []


async def test_a_domain_named_after_the_firm_and_the_reading_agree_and_a_yes_teaches_the_address(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    # Two cards for one firm, sharing a tax id, neither with a mail address.
    first = await card(company_id, "STONE GMBH", TAX)
    await card(company_id, "Stone GmbH Lojistik", TAX)
    project_id = await placed(client, model, company_id, mail("<order@x>"))

    [party] = await on(client, project_id)

    # The domain "stone.example" was registered as the firm's name: with the reading, two.
    assert (party["party_id"], party["signals"]) == (str(first), ["reading", "sender"])
    await client.patch(f"/project-parties/{party['id']}", json={"state": "confirmed"})
    async with open_session() as db:
        known = await parties.writing_from(db, company_id, "ada@stone.example")
    assert [each.id for each in known] == [first]


async def test_a_client_the_owner_set_takes_no_suggestion(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    await card(company_id, "STONE GMBH", ADA)
    other = await card(company_id, "Anvil AG")
    project_id = await placed(client, model, company_id, mail("<order@x>"))
    [suggestion] = await on(client, project_id)

    chosen = await client.post(
        "/project-parties",
        json={"project_id": project_id, "party_id": str(other), "role": "client"},
    )
    assert chosen.status_code == 201
    await suggested_again(company_id)

    assert [(each["party_id"], each["state"]) for each in await on(client, project_id)] == [
        (str(other), "confirmed")
    ]
    assert suggestion["id"] not in {each["id"] for each in await on(client, project_id)}


async def test_a_suggested_firm_picked_by_hand_is_confirmed_as_an_answer_is(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    first = await card(company_id, "STONE GMBH", TAX)
    await card(company_id, "Stone GmbH Lojistik", TAX)
    project_id = await placed(client, model, company_id, mail("<order@x>"))
    [party] = await on(client, project_id)

    picked = await client.post(
        "/project-parties",
        json={"project_id": project_id, "party_id": str(first), "role": "client"},
    )

    assert (picked.status_code, picked.json()["state"]) == (201, "confirmed")
    # Picked by hand, the sender's address is learned as on a yes.
    async with open_session() as db:
        known = await parties.writing_from(db, company_id, "ada@stone.example")
    assert [each.id for each in known] == [first]
    assert [each["id"] for each in await on(client, project_id)] == [party["id"]]


async def test_an_answer_under_another_card_of_the_firm_stands_for_the_firm(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    first = await card(company_id, "STONE GMBH", TAX)
    second = await card(company_id, "Stone GmbH Lojistik", TAX)
    project_id = await placed(client, model, company_id, mail("<order@x>"))
    # The owner sets the firm as a carrier under its other card.
    taken = await client.post(
        "/project-parties",
        json={"project_id": project_id, "party_id": str(second), "role": "subcontractor"},
    )
    assert taken.status_code == 201

    # Signals then name the firm as a carrier, under the card it is kept under.
    async with open_session() as db:
        written = await projects.suggest_parties(
            db,
            company_id,
            uuid.UUID(project_id),
            ProjectRole.SUBCONTRACTOR,
            {first: projects.Suggestion(frozenset({"reading", "sender"}), ())},
        )
        await db.commit()

    # Cards that share a tax id are one firm: the owner's word stands, nothing is asked again.
    assert written == 0
    assert {
        (each["party_id"], each["role"], each["state"]) for each in await on(client, project_id)
    } == {(str(first), "client", "suggested"), (str(second), "subcontractor", "confirmed")}


async def test_a_bill_the_owner_puts_on_a_project_names_its_firm_on_the_next_run(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    project_id = await placed(client, model, company_id, mail("<order@x>"))
    anvil = PartyCard("anvil", "ANVIL AG", PartyKind.ORGANISATION)
    sale = InvoiceRecord(
        source_id="s-1",
        direction=InvoiceDirection.RECEIVABLE,
        kind=InvoiceKind.SALES,
        raw={},
        number="S-1",
        issue_date=date(2026, 8, 3),
        party_source_id=anvil.source_id,
        party_name=anvil.name,
    )
    await books.store_records(company_id, "books", [anvil], [sale])
    [invoice] = (await client.get("/invoices")).json()["items"]
    linked = await client.post(
        "/project-links",
        json={"item_kind": "invoice", "item_id": invoice["id"], "project_id": project_id},
    )
    assert linked.status_code == 201

    # Nothing is left to place: the owner's link is what changed.
    assert await service.place_pending() == 0

    [party] = await on(client, project_id)
    assert (party["party_name"], party["signals"]) == ("ANVIL AG", ["invoice"])


async def test_cards_sharing_a_stand_in_tax_id_are_one_firm_only_where_named_alike(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    stand_in = Identifier(IdentifierKind.TAX_ID, "9999999999")
    # Books give one stand-in number to every foreign firm: the oldest card is another firm.
    await card(company_id, "Anvil AG", stand_in)
    stone = await card(company_id, "STONE GMBH", ADA, stand_in)
    project_id = await placed(client, model, company_id, mail("<order@x>"))

    [party] = await on(client, project_id)

    assert (party["party_id"], party["party_name"]) == (str(stone), "STONE GMBH")


async def test_cards_named_alike_through_another_card_are_one_firm(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    shared = Identifier(IdentifierKind.TAX_ID, "9999999999")
    # "HK ABCD" and "HK ABCE" differ, but "HK ABC" may be short for either: one firm, three cards.
    first = await card(company_id, "HK ABCD", shared)
    middle = await card(company_id, "HK ABC", shared)
    last = await card(company_id, "HK ABCE", shared)

    async with open_session() as db:
        firm = await parties.one_per_firm(db, company_id, [first, middle, last])

    assert firm == {first: first, middle: first, last: first}
