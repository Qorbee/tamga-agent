"""A record that names its firm by tax id alone finds the one card with that tax id only where the
card is named like it: books give one stand-in number to every firm without one of its own."""

import uuid
from datetime import date

import httpx
import pytest

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind, PartyKind
from app.modules.parties.service import Identifier
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration

STAND_IN = Identifier(IdentifierKind.TAX_ID, "9999999999")


def sale(source_id: str, name: str) -> InvoiceRecord:
    return InvoiceRecord(
        source_id=source_id,
        direction=InvoiceDirection.RECEIVABLE,
        kind=InvoiceKind.SALES,
        raw={},
        number=source_id.upper(),
        issue_date=date(2026, 8, 3),
        party_name=name,
        party_tax_id=STAND_IN.value,
    )


async def test_a_tax_id_alone_gives_a_record_only_a_card_named_like_it(
    client: httpx.AsyncClient,
) -> None:
    company_id: uuid.UUID = await guest_client(client)
    anvil = PartyCard("anvil", "ANVIL AG", PartyKind.ORGANISATION, identifiers=[STAND_IN])
    await books.store_records(
        company_id, "books", [anvil], [sale("s-1", "Anvil AG Zurich"), sale("s-2", "Stone GmbH")]
    )

    async with open_session() as db:
        card = await parties.get_synced_party(db, company_id, "books", anvil.source_id)
    assert card is not None
    found = {
        each["number"]: each["party_id"] for each in (await client.get("/invoices")).json()["items"]
    }

    assert found["S-1"] == str(card.id)
    # Another firm's name on the stand-in number: no card, rather than the wrong one.
    assert found["S-2"] is None
