"""Parties and the identity graph: who the company deals with, and every way each one is recognised
(docs/architecture/09, ADR-0010).

The accounting system's customer and supplier cards are the backbone: their identifiers are
confirmed. What is read from mail or documents is stored unconfirmed until the owner confirms it.
These operations run inside the caller's transaction (a books sync, a linking job), so they flush
and never commit.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.parties import repository
from app.modules.parties.identifiers import normalise, normalise_name
from app.modules.parties.models import (
    IdentifierKind,
    IdentifierSource,
    Party,
    PartyIdentifier,
    PartyKind,
)
from app.modules.parties.schemas import BalanceSide, PartyOut, PartyPage, PartySort
from app.platform.auth import Actor
from app.platform.errors import NotFound
from app.platform.pagination import PageParams

_CONFIRMING: Final = frozenset({IdentifierSource.BOOKS, IdentifierSource.OWNER})
# The words a firm's name may end with that say what kind of firm it is ("LTD. ŞTİ.", "A.Ş."), in
# the form names are compared in, longest first: each country's pack registers its own.
_LEGAL_FORMS: list[tuple[str, ...]] = []
_WORD: Final = re.compile(r"[^\W_]+")
_PARTY_SORTS: Final[dict[str, list[ColumnElement[Any]]]] = {
    "source": [Party.source.is_(None), Party.name.asc()],
    "name": [Party.name.asc()],
    "-name": [Party.name.desc()],
    "created_at": [Party.created_at.asc()],
    "-created_at": [Party.created_at.desc()],
    "balance": [Party.balance.asc().nulls_last()],
    "-balance": [Party.balance.desc().nulls_last()],
}


@dataclass(frozen=True, slots=True)
class Identifier:
    kind: IdentifierKind
    value: str


@dataclass(frozen=True, slots=True)
class PartyMatch:
    party: Party
    confirmed: bool


async def sync_party(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    source: str,
    source_id: str,
    name: str,
    kind: PartyKind,
    identifiers: Iterable[Identifier] = (),
    balance: Decimal | None = None,
    balance_currency: str | None = None,
    balance_on: date | None = None,
) -> Party:
    """The party behind an accounting card, created or updated, with the card's identifiers
    confirmed and its balance as the card gives it now. Identifiers that later leave the card are
    kept: an old phone still identifies the messages it sent."""
    party = await repository.upsert_from_source(
        db,
        company_id=company_id,
        source=source,
        source_id=source_id,
        name=name,
        kind=kind,
        balance=balance,
        balance_currency=balance_currency if balance is not None else None,
        balance_on=balance_on if balance is not None else None,
    )
    await add_identifiers(
        db,
        company_id=company_id,
        party_id=party.id,
        identifiers=[Identifier(IdentifierKind.NAME, name), *identifiers],
        source=IdentifierSource.BOOKS,
    )
    return party


async def create_party(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    name: str,
    kind: PartyKind,
    identifiers: Iterable[Identifier] = (),
    source: IdentifierSource,
) -> Party:
    party = await repository.add(db, Party(company_id=company_id, name=name, kind=kind))
    await add_identifiers(
        db,
        company_id=company_id,
        party_id=party.id,
        identifiers=[Identifier(IdentifierKind.NAME, name), *identifiers],
        source=source,
    )
    return party


async def add_identifiers(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    party_id: uuid.UUID,
    identifiers: Iterable[Identifier],
    source: IdentifierSource,
) -> int:
    """Stores each identifier that normalises; returns how many did. One that cannot be what it
    claims (a malformed IBAN, a phone fragment) is skipped rather than stored."""
    country = await _country(db, company_id)
    stored = 0
    for identifier in identifiers:
        normalised = normalise(identifier.kind, identifier.value, country=country)
        if normalised is None:
            continue
        await repository.add_identifier(
            db,
            company_id=company_id,
            party_id=party_id,
            kind=identifier.kind,
            value=identifier.value.strip()[:300],
            normalised=normalised[:300],
            source=source,
            confirmed=source in _CONFIRMING,
        )
        stored += 1
    return stored


async def find_parties(
    db: AsyncSession, *, company_id: uuid.UUID, identifier: Identifier
) -> list[PartyMatch]:
    """Every party known by this identifier, confirmed matches first. More than one is normal for a
    name or a shared office phone: the caller decides, and an unconfirmed match only suggests."""
    normalised = normalise(
        identifier.kind, identifier.value, country=await _country(db, company_id)
    )
    if normalised is None:
        return []
    rows = await repository.find_by_identifier(db, company_id, identifier.kind, normalised)
    return [PartyMatch(party=party, confirmed=found.confirmed) for party, found in rows]


async def get_parties(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Party]:
    return {party.id: party for party in await repository.get_many(db, company_id, ids)}


async def get_identifiers(
    db: AsyncSession, company_id: uuid.UUID, party_id: uuid.UUID
) -> Sequence[PartyIdentifier]:
    return await repository.get_identifiers(db, company_id, party_id)


async def _country(db: AsyncSession, company_id: uuid.UUID) -> str:
    return (await companies.get_companies(db, [company_id]))[company_id].country


async def get_synced_party(
    db: AsyncSession, company_id: uuid.UUID, source: str, source_id: str
) -> Party | None:
    """The party synced from this accounting card, if it has been."""
    return await repository.get_by_source(db, company_id, source, source_id)


# Names


def register_legal_forms(forms: Iterable[str]) -> None:
    """A pack's legal-form tails, written as firms write them ("LTD. ŞTİ."); they are compared
    without case, accents or punctuation."""
    for form in forms:
        words = tuple(normalise_name(form).split())
        if words and words not in _LEGAL_FORMS:
            _LEGAL_FORMS.append(words)
    _LEGAL_FORMS.sort(key=len, reverse=True)


def short_name(name: str) -> str:
    """A firm's name without its legal-form tail: "ATLAS NAKLİYAT SAN. VE TİC. LTD. ŞTİ." is
    "ATLAS NAKLİYAT". For a project's name and a chip; the party keeps its full name. The first
    word always stays, so no name is emptied."""
    text = " ".join(unicodedata.normalize("NFC", name).split())
    words = [(match.end(), normalise_name(match.group())) for match in _WORD.finditer(text)]
    kept = len(words)
    while kept > 1:
        tail = next(
            (
                form
                for form in _LEGAL_FORMS
                if len(form) < kept
                and tuple(word for _, word in words[kept - len(form) : kept]) == form
            ),
            None,
        )
        if tail is None:
            break
        kept -= len(tail)
    return text[: words[kept - 1][0]] if 0 < kept < len(words) else text


def name_key(name: str) -> str:
    """The form two spellings of one firm's name share: its short name without case, accents or
    punctuation."""
    return normalise_name(short_name(name))


async def get_party(db: AsyncSession, actor: Actor, party_id: uuid.UUID) -> PartyOut:
    found = await repository.get_many(db, actor.company_id, [party_id])
    if not found:
        raise NotFound("No such party")
    return PartyOut.model_validate(found[0])


async def list_parties(
    db: AsyncSession,
    actor: Actor,
    *,
    kind: PartyKind | None,
    q: str | None,
    balance_side: BalanceSide | None = None,
    sort: PartySort,
    page: PageParams,
) -> PartyPage:
    """The company's parties, by default those from the books first."""
    words = q.strip() if q else None
    rows, total = await repository.list_parties(
        db,
        actor.company_id,
        kind=kind,
        q=words,
        balance_side=balance_side,
        normalised=normalise_name(words) if words else None,
        order_by=_PARTY_SORTS[sort],
        page=page,
    )
    return PartyPage(
        items=[PartyOut.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
