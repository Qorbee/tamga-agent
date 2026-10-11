"""The freight scenarios a judge's sandbox opens with, and what happens later in their worlds.

- **One truck in, one on the road** (``freight``): one job, a client and two subcontractors. The
  first truck has delivered, its CMR is in, and the client has paid; the second is still on the
  road. So the payment path has a bill it can pay and one it must hold. The second truck's
  dispatcher writes the next morning with its CMR.
- **Short cash on Friday** (``payday``): three carriers delivered and are owed this week, a
  fourth is on the road, and the bank holds less than all of it under a reserve that keeps two
  weeks of bills. The run says which fit. A fragile carrier asks to be paid; two days on, the
  client's next payment arrives and everything fits.

The books are stored as a books source would have given them, as fixed records that do not
depend on how far back the pilot's sync reads; the projects, their trucks and their links are
written as the owner would have confirmed them. Every date is set from the sandbox's clock on
the day it opened. The places and plates belong to no country pack: the copy judges read keeps
them.
"""

import dataclasses
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.companies import service as companies
from app.modules.documents import service as documents
from app.modules.money import service as money
from app.modules.money.models import AccountKind, MovementCategory
from app.modules.money.schemas import AccountRecord, MovementRecord
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind, PartyKind
from app.modules.parties.service import Identifier
from app.modules.playground import service as playground
from app.modules.playground.service import Persona, Scenario, Step
from app.modules.policy import service as policies
from app.modules.policy.models import PaymentMethod
from app.modules.policy.schemas import PolicyIn
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource, LinkMethod, ProjectStatus
from app.packs.logistics import service
from app.packs.logistics.models import ShipmentStatus
from app.platform import clock, events
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


@dataclass(frozen=True, slots=True)
class _Invoice:
    number: str
    firm: _Firm
    direction: InvoiceDirection
    # Days from the day the sandbox opened: negative is before it.
    issued: int
    due: int
    total: Decimal
    paid: bool = False


@dataclass(frozen=True, slots=True)
class _Truck:
    plate: str
    firm: _Firm
    bill: str
    loaded: int
    # None: still on the road.
    delivered: int | None = None
    # The signed CMR's number, linked as proof once delivered.
    cmr: str | None = None


@dataclass(frozen=True, slots=True)
class _Job:
    number: str
    client: _Firm
    route: str
    starts: int
    ends: int
    trucks: tuple[_Truck, ...]
    goods: str


@dataclass(frozen=True, slots=True)
class _World:
    firms: tuple[_Firm, ...]
    invoices: tuple[_Invoice, ...]
    jobs: tuple[_Job, ...]
    accounts: tuple[AccountRecord, ...]
    # The month's movements before the sandbox opened, given the sale ids by number.
    movements: Callable[[date, dict[str, uuid.UUID]], list[MovementRecord]]
    policy: PolicyIn | None = None


# --- One truck in, one on the road -------------------------------------------------------------

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

# The job's numbers, as the books and the CMRs write them.
SALE: Final = "NW-2026-0418"
DELIVERED_BILL: Final = "AH-7731"
MOVING_BILL: Final = "KF-2209"
CMR: Final = "CMR 4471029"
MOVING_CMR: Final = "CMR 4471057"
PLATE_DELIVERED: Final = "GB AH 7 KLR"
PLATE_MOVING: Final = "PL WGM 4821"
PRICE: Final = Decimal("2400.00")
COST: Final = Decimal("1900.00")

# The company's banks, at example IBANs no real account holds.
ACCOUNTS: Final = (
    AccountRecord(
        source_ref="eur",
        kind=AccountKind.BANK,
        name="Main account",
        bank="Harbour Bank",
        iban="NL91ABNA0417164300",
        currency=_EUR,
        balance=Decimal("18400.00"),
    ),
    AccountRecord(
        source_ref="gbp",
        kind=AccountKind.BANK,
        name="Sterling account",
        bank="Harbour Bank",
        iban="GB29NWBK60161331926819",
        currency="GBP",
        balance=Decimal("4100.00"),
    ),
    AccountRecord(
        source_ref="usd",
        kind=AccountKind.BANK,
        name="Dollar account",
        bank="Meridian Bank",
        iban="FR1420041010050500013M02606",
        currency="USD",
        balance=Decimal("6200.00"),
    ),
)
_CURRENCIES: Final = {account.source_ref: account.currency for account in ACCOUNTS}


def _moved(
    today: date,
    ref: str,
    account: str,
    days_ago: int,
    amount: str,
    category: MovementCategory,
    what: str,
) -> MovementRecord:
    return MovementRecord(
        source_ref=ref,
        account_ref=account,
        booked_on=today - timedelta(days=days_ago),
        amount=Decimal(amount),
        currency=_CURRENCIES[account],
        category=category,
        description=what,
    )


def _receipt(
    today: date, ref: str, days_ago: int, firm: _Firm, number: str, amount: Decimal, sale: uuid.UUID
) -> MovementRecord:
    return dataclasses.replace(
        _moved(
            today,
            ref,
            "eur",
            days_ago,
            str(amount),
            MovementCategory.CLIENT_RECEIPT,
            f"Payment {number}",
        ),
        counterparty=firm.name,
        counterparty_iban=firm.iban,
        counterparty_tax_id=firm.tax_id,
        reference=number,
        invoice_id=sale,
    )


def _freight_movements(today: date, sales: dict[str, uuid.UUID]) -> list[MovementRecord]:
    """A month through the three accounts: the client's payment of the sale, and the money that
    is about no job, a salary, a tax, a fee, a partner's drawing, a transfer between two of the
    company's own accounts."""
    return [
        _moved(today, "m1", "eur", 20, "-3200.00", MovementCategory.SALARY, "Salaries, last month"),
        _moved(today, "m2", "eur", 20, "-45.00", MovementCategory.BANK_FEE, "Account fee"),
        _moved(today, "m3", "eur", 12, "-1150.00", MovementCategory.TAX, "VAT payment"),
        _moved(today, "m4", "usd", 10, "-640.00", MovementCategory.OTHER, "Fuel cards"),
        _moved(
            today,
            "m5",
            "eur",
            8,
            "-2000.00",
            MovementCategory.PARTNER_WITHDRAWAL,
            "Partner drawing",
        ),
        _receipt(today, "m6", 6, CLIENT, SALE, PRICE * 2, sales[SALE]),
        _moved(
            today,
            "m7",
            "eur",
            4,
            "-1500.00",
            MovementCategory.OWN_TRANSFER,
            "To the sterling account",
        ),
        _moved(
            today, "m8", "gbp", 4, "1290.00", MovementCategory.OWN_TRANSFER, "From the main account"
        ),
    ]


FREIGHT: Final = _World(
    firms=(CLIENT, DELIVERED, MOVING),
    invoices=(
        # The client's invoice for both trucks, already paid.
        _Invoice(SALE, CLIENT, InvoiceDirection.RECEIVABLE, -12, -2, PRICE * 2, paid=True),
        # The bill of the truck that delivered: proof in, client paid, due in three days.
        _Invoice(DELIVERED_BILL, DELIVERED, InvoiceDirection.PAYABLE, -6, 3, COST),
        # The bill of the truck still on the road: sent early, nothing proves the delivery.
        _Invoice(MOVING_BILL, MOVING, InvoiceDirection.PAYABLE, -2, 10, COST),
    ),
    jobs=(
        _Job(
            SALE,
            CLIENT,
            "Rotterdam → Munich",
            starts=-14,
            ends=2,
            goods="33 pallets, household textiles, 18,400 kg",
            trucks=(
                _Truck(
                    PLATE_DELIVERED, DELIVERED, DELIVERED_BILL, loaded=-14, delivered=-9, cmr=CMR
                ),
                _Truck(PLATE_MOVING, MOVING, MOVING_BILL, loaded=-3),
            ),
        ),
    ),
    accounts=ACCOUNTS,
    movements=_freight_movements,
)

DISPATCHER: Final = Persona(
    key="marta",
    name="Marta Nowak",
    role=f"Dispatcher, {MOVING.name}",
    address="marta.nowak@kestrel-freight.example",
)


async def _moving_truck_delivered(company_id: uuid.UUID) -> None:
    opened = await _opened_on(company_id)
    await playground.say(
        company_id,
        DISPATCHER,
        "freight.delivered",
        f"Good morning! {PLATE_MOVING} unloaded in Munich at 07:40, all 33 pallets fine. "
        f"Signed {MOVING_CMR} attached. Invoice {MOVING_BILL} stands as sent.",
        [
            (
                f"{MOVING_CMR} - {MOVING.name}.pdf",
                _cmr_pdf(
                    MOVING_CMR, FREIGHT.jobs[0], MOVING, PLATE_MOVING, opened + timedelta(days=1)
                ),
                "application/pdf",
            )
        ],
    )


async def _moving_asks_to_be_paid(company_id: uuid.UUID) -> None:
    await playground.say(
        company_id,
        DISPATCHER,
        "freight.asks",
        f"Hi, a quick one from our accounts: when can we expect {MOVING_BILL}? Thanks, Marta",
    )


# --- Short cash on Friday ----------------------------------------------------------------------

BUYER: Final = _Firm(
    "C-2", "Brightwater Foods B.V.", PartyKind.ORGANISATION, "NL853746333B01", "NL02ABNA0123456789"
)
FRAGILE: Final = _Firm(
    "S-3",
    "Orbit Transport s.r.o.",
    PartyKind.ORGANISATION,
    "CZ27082440",
    "CZ6508000000192000145399",
)
LATER: Final = _Firm(
    "S-4",
    "Vistula Cargo S.A.",
    PartyKind.ORGANISATION,
    "PL7792433421",
    "PL27114020040000300201355387",
)

RUN_SALE: Final = "BW-2026-0611"
NEXT_SALE: Final = "BW-2026-0640"
RUN_BILLS: Final = ("AH-7790", "KF-2241", "OT-0388")
LATER_BILL: Final = "VC-1150"
NEXT_PAYMENT: Final = Decimal("6500.00")

_RUN_ACCOUNTS: Final = (
    dataclasses.replace(ACCOUNTS[0], balance=Decimal("4200.00")),
    dataclasses.replace(ACCOUNTS[2], balance=Decimal("900.00")),
)


def _run_movements(today: date, sales: dict[str, uuid.UUID]) -> list[MovementRecord]:
    return [
        _moved(today, "m1", "eur", 21, "-3400.00", MovementCategory.SALARY, "Salaries, last month"),
        _moved(today, "m2", "eur", 21, "-45.00", MovementCategory.BANK_FEE, "Account fee"),
        _moved(today, "m3", "eur", 14, "-1320.00", MovementCategory.TAX, "VAT payment"),
        _moved(today, "m4", "usd", 9, "-610.00", MovementCategory.OTHER, "Fuel cards"),
        _receipt(today, "m5", 5, BUYER, RUN_SALE, Decimal("7600.00"), sales[RUN_SALE]),
        _moved(today, "m6", "eur", 3, "-2950.00", MovementCategory.LOAN, "Truck lease"),
    ]


PAYDAY: Final = _World(
    firms=(BUYER, DELIVERED, MOVING, FRAGILE, LATER),
    invoices=(
        _Invoice(
            RUN_SALE, BUYER, InvoiceDirection.RECEIVABLE, -10, -3, Decimal("7600.00"), paid=True
        ),
        # The client's next invoice: due tomorrow, paid two days on.
        _Invoice(NEXT_SALE, BUYER, InvoiceDirection.RECEIVABLE, -6, 1, NEXT_PAYMENT),
        _Invoice(RUN_BILLS[0], DELIVERED, InvoiceDirection.PAYABLE, -9, -1, Decimal("1100.00")),
        _Invoice(RUN_BILLS[1], MOVING, InvoiceDirection.PAYABLE, -6, 2, Decimal("1300.00")),
        _Invoice(RUN_BILLS[2], FRAGILE, InvoiceDirection.PAYABLE, -3, 3, Decimal("1700.00")),
        # On the road: not payable, but the reserve keeps it, due within its two weeks.
        _Invoice(LATER_BILL, LATER, InvoiceDirection.PAYABLE, -1, 11, Decimal("1000.00")),
    ),
    jobs=(
        _Job(
            RUN_SALE,
            BUYER,
            "Hamburg → Milan",
            starts=-8,
            ends=-2,
            goods="66 pallets, canned food, 39,000 kg",
            trucks=(
                _Truck(
                    "GB AH 9 PLT",
                    DELIVERED,
                    RUN_BILLS[0],
                    loaded=-8,
                    delivered=-4,
                    cmr="CMR 5520113",
                ),
                _Truck(
                    "PL WGM 5530", MOVING, RUN_BILLS[1], loaded=-7, delivered=-3, cmr="CMR 5520127"
                ),
                _Truck(
                    "1AB 2345", FRAGILE, RUN_BILLS[2], loaded=-6, delivered=-2, cmr="CMR 5520138"
                ),
            ),
        ),
        _Job(
            NEXT_SALE,
            BUYER,
            "Hamburg → Lyon",
            starts=-2,
            ends=3,
            goods="33 pallets, canned food, 19,500 kg",
            trucks=(_Truck("WGM 77KX", LATER, LATER_BILL, loaded=-2),),
        ),
    ),
    accounts=_RUN_ACCOUNTS,
    movements=_run_movements,
    policy=PolicyIn(
        currency=_EUR,
        approval_limit=Decimal("0.00"),
        pay_when_paid=True,
        methods=[PaymentMethod.VAULT_USDC],
        default_method=PaymentMethod.VAULT_USDC,
        reserve_rule="steady",
        payment_weekday=4,
    ),
)

CARRIER: Final = Persona(
    key="petr",
    name="Petr Dvořák",
    role=f"Owner, {FRAGILE.name}",
    address="petr@orbit-transport.example",
)


async def _fragile_asks(company_id: uuid.UUID) -> None:
    await playground.say(
        company_id,
        CARRIER,
        "payday.asks",
        f"Hello, our truck unloaded in Milan two days ago, CMR is with you. Could {RUN_BILLS[2]} "
        "be paid this week? Our fuel cards are due on Friday.",
    )


async def _fragile_asks_again(company_id: uuid.UUID) -> None:
    await playground.say(
        company_id,
        CARRIER,
        "payday.asks_again",
        f"Good morning, any news on {RUN_BILLS[2]}? Sorry to push, it's a tight week.",
    )


async def _client_pays_next(company_id: uuid.UUID) -> None:
    """The client's next invoice is paid: the books say so, and the bank holds it."""
    opened = await _opened_on(company_id)
    paid = frozenset({NEXT_SALE})
    await books.store_records(
        company_id, SOURCE, _cards(PAYDAY, opened, paid), _records(PAYDAY, opened, paid)
    )
    # The open pages read the invoice again: it is paid now.
    await events.publish_now(company_id, [events.EventKind.INVOICES])
    async with open_session() as db:
        sale = await _invoice_ids(db, company_id, [NEXT_SALE])
        company = (await companies.get_companies(db, [company_id]))[company_id]
        today = clock.now(company).date()
    main = _RUN_ACCOUNTS[0]
    await money.store(
        company_id,
        SOURCE,
        [dataclasses.replace(main, balance=(main.balance or Decimal(0)) + NEXT_PAYMENT)],
        [_receipt(today, "m7", 0, BUYER, NEXT_SALE, NEXT_PAYMENT, sale[_as_compared(NEXT_SALE)])],
    )


# --- The scenarios -----------------------------------------------------------------------------


async def seed_freight(company_id: uuid.UUID) -> None:
    await _seed(company_id, FREIGHT)


async def seed_payday(company_id: uuid.UUID) -> None:
    await _seed(company_id, PAYDAY)


SCENARIOS: Final = (
    Scenario(
        key="freight",
        title="One truck in, one on the road",
        summary="A job with two carriers: one delivered and can be paid, one is still driving. "
        "Skip a day and its dispatcher sends the CMR.",
        seed=seed_freight,
        personas=(DISPATCHER,),
        steps=(Step(1, _moving_truck_delivered), Step(2, _moving_asks_to_be_paid)),
    ),
    Scenario(
        key="payday",
        title="Short cash on Friday",
        summary="Three carriers are owed this week and the bank can't cover all of them under "
        "the reserve. Watch the payment run decide, then skip until the client pays.",
        seed=seed_payday,
        personas=(CARRIER,),
        steps=(
            Step(0, _fragile_asks),
            Step(1, _fragile_asks_again),
            Step(2, _client_pays_next),
        ),
    ),
)


async def _seed(company_id: uuid.UUID, world: _World) -> None:
    """Fills a sandbox with a scenario. Seeding it again changes nothing: the books are upserted
    on their own ids, and a sandbox that already holds the first job is left as it is."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id]))[company_id]
        today = clock.now(company).date()
        # Looked for first: a second run on a later day must not re-date the books it stored.
        if await projects.find_by_number(db, company_id, world.jobs[0].number) is not None:
            return
    if world.policy is not None:
        async with open_session() as db:
            await policies.seed_policy(db, company_id, world.policy)
    await books.store_records(company_id, SOURCE, _cards(world, today), _records(world, today))

    async with open_session() as db:
        firms = {}
        for firm in world.firms:
            party = await parties.get_synced_party(db, company_id, SOURCE, firm.card)
            if party is None:
                raise RuntimeError(f"the scenario's books did not bring {firm.card}")
            firms[firm.card] = party.id
        bills = await _invoice_ids(db, company_id, [invoice.number for invoice in world.invoices])
        for job in world.jobs:
            await _add_job(db, company_id, job, today, firms, bills)
        await db.commit()
    sales = {
        invoice.number: bills[_as_compared(invoice.number)]
        for invoice in world.invoices
        if invoice.direction is InvoiceDirection.RECEIVABLE
    }
    await money.store(company_id, SOURCE, list(world.accounts), world.movements(today, sales))


async def _add_job(
    db: AsyncSession,
    company_id: uuid.UUID,
    job: _Job,
    today: date,
    firms: dict[str, uuid.UUID],
    bills: dict[str, uuid.UUID],
) -> None:
    def day(offset: int) -> date:
        return today + timedelta(days=offset)

    project = await projects.new_project(
        db,
        company_id=company_id,
        name=f"{job.client.name} · {job.route} · {day(job.starts):%d.%m.%Y}",
        number=job.number,
        status=ProjectStatus.DRAFT,
        source=FieldSource.OWNER,
        starts_on=day(job.starts),
        ends_on=day(job.ends),
    )
    links: list[tuple[ItemKind, uuid.UUID, uuid.UUID | None, bool]] = [
        (ItemKind.INVOICE, bills[_as_compared(job.number)], None, False)
    ]
    for truck in job.trucks:
        shipment = await service.add_shipment(db, company_id=company_id, project_id=project.id)
        values: dict[str, object] = {
            "truck_plate": truck.plate,
            "subcontractor_id": firms[truck.firm.card],
            "loading_date": day(truck.loaded),
            "status": ShipmentStatus.ON_ROAD,
        }
        if truck.delivered is not None:
            values |= {"delivery_date": day(truck.delivered), "status": ShipmentStatus.DELIVERED}
        await service.fill_shipment(db, shipment, values, source=FieldSource.OWNER)
        links.append((ItemKind.INVOICE, bills[_as_compared(truck.bill)], shipment.id, False))
        if truck.cmr is not None and truck.delivered is not None:
            cmr = await documents.store_file(
                db,
                company_id=company_id,
                data=_cmr_pdf(truck.cmr, job, truck.firm, truck.plate, day(truck.delivered)),
                filename=f"{truck.cmr} - {truck.firm.name}.pdf",
                content_type="application/pdf",
            )
            await documents.request_reading(db, company_id, [cmr.id])
            links.append((ItemKind.DOCUMENT, cmr.id, shipment.id, True))
    for kind, item_id, shipment_id, proof in links:
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


def _cards(world: _World, today: date, paid: frozenset[str] = frozenset()) -> list[PartyCard]:
    """The customer and supplier cards, with the balances their invoices leave."""
    owed: dict[str, Decimal] = {firm.card: Decimal("0.00") for firm in world.firms}
    for invoice in world.invoices:
        if invoice.paid or invoice.number in paid:
            continue
        sign = 1 if invoice.direction is InvoiceDirection.RECEIVABLE else -1
        owed[invoice.firm.card] += sign * invoice.total
    return [
        PartyCard(
            source_id=firm.card,
            name=firm.name,
            kind=firm.kind,
            identifiers=[
                Identifier(IdentifierKind.TAX_ID, firm.tax_id),
                Identifier(IdentifierKind.IBAN, firm.iban),
            ],
            balance=owed[firm.card],
            balance_currency=_EUR,
            balance_on=today,
        )
        for firm in world.firms
    ]


def _records(world: _World, today: date, paid: frozenset[str] = frozenset()) -> list[InvoiceRecord]:
    return [
        _record(invoice, today, invoice.paid or invoice.number in paid)
        for invoice in world.invoices
    ]


def _record(invoice: _Invoice, today: date, paid: bool) -> InvoiceRecord:
    net = (invoice.total / Decimal("1.19")).quantize(Decimal("0.01"))
    receivable = invoice.direction is InvoiceDirection.RECEIVABLE
    return InvoiceRecord(
        source_id=invoice.number,
        direction=invoice.direction,
        kind=InvoiceKind.SALES if receivable else InvoiceKind.PURCHASE,
        raw={"number": invoice.number, "scenario": "freight"},
        number=invoice.number,
        party_source_id=invoice.firm.card,
        party_name=invoice.firm.name,
        issue_date=today + timedelta(days=invoice.issued),
        due_date=today + timedelta(days=invoice.due),
        currency=_EUR,
        net_amount=net,
        tax_amount=invoice.total - net,
        total_amount=invoice.total,
        remaining_amount=Decimal("0.00") if paid else invoice.total,
        status="paid" if paid else "open",
    )


async def _invoice_ids(
    db: AsyncSession, company_id: uuid.UUID, numbers: Sequence[str]
) -> dict[str, uuid.UUID]:
    return {
        _as_compared(bill.number or ""): bill.id
        for bill in await books.invoices_named(
            db, company_id, numbers=[_as_compared(n) for n in numbers], e_document_ids=[]
        )
    }


async def _opened_on(company_id: uuid.UUID) -> date:
    opened = await playground.opened_on(company_id)
    if opened is None:
        raise RuntimeError(f"{company_id} has no world that opened")
    return opened


def _as_compared(number: str) -> str:
    """An invoice number as the books compare one: its letters and digits, upper case."""
    return "".join(ch for ch in number if ch.isalnum()).upper()


def _cmr_pdf(number: str, job: _Job, carrier: _Firm, plate: str, delivered_on: date) -> bytes:
    """A one-page PDF with a text layer, standing in for the signed CMR a driver brings back."""
    origin, _, destination = job.route.partition(" → ")
    lines = [
        "CMR - International Consignment Note",
        f"No. {number.removeprefix('CMR ')}",
        f"Sender: {job.client.name}, {origin}",
        f"Consignee: {job.client.name} Distribution Centre, {destination}",
        f"Carrier: {carrier.name}, truck {plate}",
        f"Goods: {job.goods}",
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
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
