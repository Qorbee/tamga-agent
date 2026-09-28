import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.parties import service
from app.modules.parties.models import IdentifierKind, IdentifierSource, PartyKind
from app.modules.parties.service import Identifier

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
