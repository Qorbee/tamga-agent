from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.parties import service
from app.modules.parties.models import IdentifierKind, IdentifierSource, PartyKind
from app.modules.parties.service import Identifier
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration

PHONE = Identifier(IdentifierKind.PHONE, "0532 123 45 67")
EMAIL = Identifier(IdentifierKind.EMAIL, "muhasebe@kaya.example")


async def test_a_card_synced_twice_is_one_party_with_its_latest_name(db: AsyncSession) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)

    first = await service.sync_party(
        db,
        company_id=company.id,
        source="books",
        source_id="120",
        name="Kaya Nakliyat",
        kind=PartyKind.ORGANISATION,
        identifiers=[PHONE, EMAIL, Identifier(IdentifierKind.IBAN, "TR00 1234")],
    )
    again = await service.sync_party(
        db,
        company_id=company.id,
        source="books",
        source_id="120",
        name="Kaya Nakliyat Ltd. Şti.",
        kind=PartyKind.ORGANISATION,
        identifiers=[Identifier(IdentifierKind.PHONE, "+90 532 1234567")],
    )

    assert again.id == first.id and again.name == "Kaya Nakliyat Ltd. Şti."
    identifiers = await service.get_identifiers(db, company.id, first.id)
    # Both names, one phone however written, the email; the broken IBAN was never stored.
    assert sorted((i.kind, i.normalised) for i in identifiers) == [
        (IdentifierKind.EMAIL, "muhasebe@kaya.example"),
        (IdentifierKind.NAME, "kaya nakliyat"),
        (IdentifierKind.NAME, "kaya nakliyat ltd sti"),
        (IdentifierKind.PHONE, "+905321234567"),
    ]
    assert all(i.confirmed and i.source is IdentifierSource.BOOKS for i in identifiers)


async def test_an_extracted_identifier_suggests_until_the_books_confirm_it(
    db: AsyncSession,
) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    party = await service.create_party(
        db,
        company_id=company.id,
        name="Mehmet Yılmaz",
        kind=PartyKind.PERSON,
        identifiers=[PHONE],
        source=IdentifierSource.EXTRACTION,
    )

    [match] = await service.find_parties(
        db, company_id=company.id, identifier=Identifier(IdentifierKind.PHONE, "905321234567")
    )
    assert match.party.id == party.id and not match.confirmed

    await service.add_identifiers(
        db,
        company_id=company.id,
        party_id=party.id,
        identifiers=[PHONE],
        source=IdentifierSource.OWNER,
    )
    [match] = await service.find_parties(db, company_id=company.id, identifier=PHONE)
    assert match.confirmed


async def test_one_phone_can_name_two_parties_and_confirmed_comes_first(db: AsyncSession) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    guess = await service.create_party(
        db,
        company_id=company.id,
        name="Ahmet (driver)",
        kind=PartyKind.PERSON,
        identifiers=[PHONE],
        source=IdentifierSource.EXTRACTION,
    )
    card = await service.sync_party(
        db,
        company_id=company.id,
        source="books",
        source_id="7",
        name="Kaya Nakliyat",
        kind=PartyKind.ORGANISATION,
        identifiers=[PHONE],
    )

    matches = await service.find_parties(db, company_id=company.id, identifier=PHONE)

    assert [(m.party.id, m.confirmed) for m in matches] == [(card.id, True), (guess.id, False)]


async def test_another_company_never_finds_the_party(db: AsyncSession) -> None:
    ours = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    theirs = await companies.create_company(db, name="Sandbox", kind=CompanyKind.SANDBOX)
    party = await service.sync_party(
        db,
        company_id=ours.id,
        source="books",
        source_id="120",
        name="Kaya Nakliyat",
        kind=PartyKind.ORGANISATION,
        identifiers=[EMAIL],
    )
    # The same card id in another company is another party.
    other = await service.sync_party(
        db,
        company_id=theirs.id,
        source="books",
        source_id="120",
        name="Kaya Nakliyat",
        kind=PartyKind.ORGANISATION,
    )

    assert other.id != party.id
    assert await service.find_parties(db, company_id=theirs.id, identifier=EMAIL) == []
    assert await service.get_parties(db, theirs.id, [party.id]) == {}
    count = await db.scalar(select(func.count()).select_from(type(party)))
    assert count == 2


async def test_an_identifier_cannot_join_another_companys_party(db: AsyncSession) -> None:
    ours = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    theirs = await companies.create_company(db, name="Sandbox", kind=CompanyKind.SANDBOX)
    party = await service.create_party(
        db,
        company_id=ours.id,
        name="Kaya Nakliyat",
        kind=PartyKind.ORGANISATION,
        source=IdentifierSource.OWNER,
    )
    await db.commit()

    # The database refuses the pairing, whatever a caller passes (ADR-0003).
    with pytest.raises(IntegrityError):
        await service.add_identifiers(
            db,
            company_id=theirs.id,
            party_id=party.id,
            identifiers=[EMAIL],
            source=IdentifierSource.OWNER,
        )


async def test_parties_are_listed_for_choosing_one_those_from_the_books_first(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        await service.create_party(
            db,
            company_id=company_id,
            name="Atlas Haulage",
            kind=PartyKind.ORGANISATION,
            source=IdentifierSource.EXTRACTION,
        )
        await service.sync_party(
            db,
            company_id=company_id,
            source="books",
            source_id="7",
            name="ÖZDEMİR Nakliyat",
            kind=PartyKind.ORGANISATION,
        )
        await service.create_party(
            db,
            company_id=company_id,
            name="Ada Driver",
            kind=PartyKind.PERSON,
            source=IdentifierSource.EXTRACTION,
        )
        await db.commit()

    listed = (await client.get("/parties")).json()
    assert [party["name"] for party in listed["items"]] == [
        "ÖZDEMİR Nakliyat",
        "Ada Driver",
        "Atlas Haulage",
    ]
    assert listed["items"][0]["source"] == "books"
    by_name = (await client.get("/parties", params={"sort": "-name"})).json()
    assert [party["name"] for party in by_name["items"]] == [
        "ÖZDEMİR Nakliyat",
        "Atlas Haulage",
        "Ada Driver",
    ]
    # Typed without the Turkish letters, the name is still found.
    found = (await client.get("/parties", params={"q": "ozdemir"})).json()
    assert [party["name"] for party in found["items"]] == ["ÖZDEMİR Nakliyat"]
    firms = (await client.get("/parties", params={"kind": "organisation"})).json()
    assert firms["total"] == 2
    # Another company sees none of them.
    await guest_client(client)
    assert (await client.get("/parties")).json()["total"] == 0


async def test_firms_are_listed_by_what_the_books_say_each_owes(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        for card, name, balance in (
            ("1", "Client Owing", "1500.00"),
            ("2", "Client Settled", "0.00"),
            ("3", "Carrier Owed", "-900.00"),
            ("4", "Client Owing More", "4000.00"),
        ):
            await service.sync_party(
                db,
                company_id=company_id,
                source="books",
                source_id=card,
                name=name,
                kind=PartyKind.ORGANISATION,
                balance=Decimal(balance),
                balance_currency="EUR",
                balance_on=date(2026, 10, 3),
            )
        # Read from mail: the books say nothing of what it owes.
        await service.create_party(
            db,
            company_id=company_id,
            name="Atlas Haulage",
            kind=PartyKind.ORGANISATION,
            source=IdentifierSource.EXTRACTION,
        )
        await db.commit()

    async def names(**params: str) -> list[str]:
        page = (await client.get("/parties", params=params)).json()
        return [party["name"] for party in page["items"]]

    assert await names(balance_side="receivable", sort="-balance") == [
        "Client Owing More",
        "Client Owing",
    ]
    assert await names(balance_side="payable", sort="balance") == ["Carrier Owed"]
    # The most owed to the company first, and a party with no balance last.
    assert (await names(sort="-balance"))[-1] == "Atlas Haulage"
    owing = (await client.get("/parties", params={"balance_side": "receivable"})).json()["items"]
    assert {(p["balance"], p["balance_currency"], p["balance_on"]) for p in owing} == {
        ("1500.00", "EUR", "2026-10-03"),
        ("4000.00", "EUR", "2026-10-03"),
    }
    # One firm, as it is now, for a page that shows the firm it was opened on.
    one = await client.get(f"/parties/{owing[0]['id']}")
    assert one.status_code == 200 and one.json() == owing[0]
    await guest_client(client)
    assert (await client.get(f"/parties/{owing[0]['id']}")).status_code == 404
