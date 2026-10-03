"""The freight scenario a judge's sandbox opens with: one job, a client and two subcontractors,
two trucks, and the books behind them. The first truck has delivered, its CMR is in, and the
client has paid; the second is still on the road. So the payment path has a bill it can pay and
one it must hold.

The books are stored as a books source would have given them, as fixed records that do not
depend on how far back the pilot's sync reads; the project, its trucks and their links are
written as the owner would have confirmed them. Every date is set from the sandbox's own clock,
once. The places and plates belong to no country pack: the copy judges read keeps them.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.companies import service as companies
from app.modules.documents import service as documents
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind, PartyKind
from app.modules.parties.service import Identifier
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource, LinkMethod, ProjectStatus
from app.packs.logistics import service
from app.packs.logistics.models import ShipmentStatus
from app.platform import clock
from app.platform.db import open_session
from app.platform.items import ItemKind

SOURCE: Final = "sandbox"
_EUR: Final = "EUR"


@dataclass(frozen=True, slots=True)
class _Firm:
    card: str
    name: str
    kind: PartyKind
    tax_id: str
    iban: str


CLIENT: Final = _Firm(
    "C-1", "Northwind Retail GmbH", PartyKind.ORGANISATION, "DE811907980", "DE89370400440532013000"
)
DELIVERED: Final = _Firm(
    "S-1", "Atlas Haulage Ltd", PartyKind.ORGANISATION, "GB980780684", "GB33BUKB20201555555555"
)
MOVING: Final = _Firm(
    "S-2",
    "Kestrel Freight Sp. z o.o.",
    PartyKind.ORGANISATION,
    "PL5260250995",
    "PL61109010140000071219812874",
)

# The job's numbers, as the books and the CMR write them.
SALE: Final = "NW-2026-0418"
DELIVERED_BILL: Final = "AH-7731"
MOVING_BILL: Final = "KF-2209"
CMR: Final = "CMR 4471029"
PLATE_DELIVERED: Final = "GB AH 7 KLR"
PLATE_MOVING: Final = "PL WGM 4821"
PRICE: Final = Decimal("2400.00")
COST: Final = Decimal("1900.00")


def cards() -> list[PartyCard]:
    """The scenario's customer and supplier cards."""
    return [
        PartyCard(
            source_id=firm.card,
            name=firm.name,
            kind=firm.kind,
            identifiers=[
                Identifier(IdentifierKind.TAX_ID, firm.tax_id),
                Identifier(IdentifierKind.IBAN, firm.iban),
            ],
        )
        for firm in (CLIENT, DELIVERED, MOVING)
    ]


def books_of(today: date) -> list[InvoiceRecord]:
    """The scenario's invoices, dated from ``today``."""
    return [
        # The client's invoice for both trucks, already paid.
        _invoice(
            SALE,
            InvoiceDirection.RECEIVABLE,
            InvoiceKind.SALES,
            CLIENT,
            issued=today - timedelta(days=12),
            due=today - timedelta(days=2),
            total=PRICE * 2,
            remaining=Decimal("0.00"),
            status="paid",
        ),
        # The bill of the truck that delivered: proof in, client paid, due in three days.
        _invoice(
            DELIVERED_BILL,
            InvoiceDirection.PAYABLE,
            InvoiceKind.PURCHASE,
            DELIVERED,
            issued=today - timedelta(days=6),
            due=today + timedelta(days=3),
            total=COST,
            remaining=COST,
            status="open",
        ),
        # The bill of the truck still on the road: sent early, nothing proves the delivery.
        _invoice(
            MOVING_BILL,
            InvoiceDirection.PAYABLE,
            InvoiceKind.PURCHASE,
            MOVING,
            issued=today - timedelta(days=2),
            due=today + timedelta(days=10),
            total=COST,
            remaining=COST,
            status="open",
        ),
    ]


def _invoice(
    number: str,
    direction: InvoiceDirection,
    kind: InvoiceKind,
    firm: _Firm,
    *,
    issued: date,
    due: date,
    total: Decimal,
    remaining: Decimal,
    status: str,
) -> InvoiceRecord:
    net = (total / Decimal("1.19")).quantize(Decimal("0.01"))
    return InvoiceRecord(
        source_id=number,
        direction=direction,
        kind=kind,
        raw={"number": number, "scenario": "freight"},
        number=number,
        party_source_id=firm.card,
        party_name=firm.name,
        issue_date=issued,
        due_date=due,
        currency=_EUR,
        net_amount=net,
        tax_amount=total - net,
        total_amount=total,
        remaining_amount=remaining,
        status=status,
    )


async def seed(company_id: uuid.UUID) -> None:
    """Fills a sandbox with the scenario. Seeding it again changes nothing: the books are
    upserted on their own ids, and a sandbox that already holds the job is left as it is."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id]))[company_id]
        today = clock.now(company).date()
        # Looked for first: a second run on a later day must not re-date the books it stored.
        if await projects.find_by_number(db, company_id, SALE) is not None:
            return
    await books.store_records(company_id, SOURCE, cards(), books_of(today))

    async with open_session() as db:
        firms = {}
        for firm in (CLIENT, DELIVERED, MOVING):
            party = await parties.get_synced_party(db, company_id, SOURCE, firm.card)
            if party is None:
                raise RuntimeError(f"the scenario's books did not bring {firm.card}")
            firms[firm.card] = party.id
        bills = {
            _as_compared(bill.number or ""): bill.id
            for bill in await books.invoices_named(
                db,
                company_id,
                numbers=[_as_compared(n) for n in (SALE, DELIVERED_BILL, MOVING_BILL)],
                e_document_ids=[],
            )
        }

        project = await projects.new_project(
            db,
            company_id=company_id,
            name=f"{CLIENT.name} · Rotterdam → Munich · {today - timedelta(days=14):%d.%m.%Y}",
            number=SALE,
            status=ProjectStatus.DRAFT,
            source=FieldSource.OWNER,
            starts_on=today - timedelta(days=14),
            ends_on=today + timedelta(days=2),
        )
        delivered = await service.add_shipment(db, company_id=company_id, project_id=project.id)
        await service.fill_shipment(
            db,
            delivered,
            {
                "truck_plate": PLATE_DELIVERED,
                "subcontractor_id": firms[DELIVERED.card],
                "loading_date": today - timedelta(days=14),
                "delivery_date": today - timedelta(days=9),
                "status": ShipmentStatus.DELIVERED,
            },
            source=FieldSource.OWNER,
        )
        moving = await service.add_shipment(db, company_id=company_id, project_id=project.id)
        await service.fill_shipment(
            db,
            moving,
            {
                "truck_plate": PLATE_MOVING,
                "subcontractor_id": firms[MOVING.card],
                "loading_date": today - timedelta(days=3),
                "status": ShipmentStatus.ON_ROAD,
            },
            source=FieldSource.OWNER,
        )

        cmr = await documents.store_file(
            db,
            company_id=company_id,
            data=_cmr_pdf(today - timedelta(days=9)),
            filename="CMR 4471029 - Atlas Haulage.pdf",
            content_type="application/pdf",
        )
        await documents.request_reading(db, company_id, [cmr.id])

        for kind, item_id, shipment_id, proof in (
            (ItemKind.INVOICE, bills[_as_compared(SALE)], None, False),
            (ItemKind.INVOICE, bills[_as_compared(DELIVERED_BILL)], delivered.id, False),
            (ItemKind.INVOICE, bills[_as_compared(MOVING_BILL)], moving.id, False),
            (ItemKind.DOCUMENT, cmr.id, delivered.id, True),
        ):
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=kind,
                item_id=item_id,
                project_id=project.id,
                method=LinkMethod.OWNER,
                confidence=1.0,
                shipment_id=shipment_id,
                proof_of_delivery=proof,
                confirm=True,
            )
        await db.commit()


def _as_compared(number: str) -> str:
    """An invoice number as the books compare one: its letters and digits, upper case."""
    return "".join(ch for ch in number if ch.isalnum()).upper()


def _cmr_pdf(delivered_on: date) -> bytes:
    """A one-page PDF with a text layer, standing in for the signed CMR a driver brings back."""
    lines = [
        "CMR - International Consignment Note",
        f"No. {CMR.removeprefix('CMR ')}",
        f"Sender: {CLIENT.name}, Rotterdam",
        "Consignee: Northwind Retail Distribution Centre, Munich",
        f"Carrier: {DELIVERED.name}, truck {PLATE_DELIVERED}",
        "Goods: 33 pallets, household textiles, 18,400 kg",
        f"Received in good order on {delivered_on:%d.%m.%Y}, signed and stamped by the consignee.",
    ]
    text = "".join(
        f"BT /F1 11 Tf 56 {760 - 22 * row} Td ({line}) Tj ET\n" for row, line in enumerate(lines)
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(text) + text + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
