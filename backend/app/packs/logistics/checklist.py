"""A freight job's checklist: for each truck its loading photo, CMR, proof of delivery and the
subcontractor's bill; for the job the client's order and invoice, and both sides paid.

A paper counts when a decision may rest on its link (`projects.is_evidence`, ADR-0010): the owner
confirmed it, or a key names it surely; any other suggestion leaves the item waiting on the owner.
A proof of delivery counts only as the payment rules count it (ADR-0021, `service.proofs`): a CMR
or photo placed on the truck but not marked by the owner leaves it waiting, and the owner's
"done" never fills it. A bill naming no truck counts for each truck whose plate it names.
"""

import uuid
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.schemas import InvoiceOut
from app.modules.documents import service as documents
from app.modules.facts import service as facts
from app.modules.projects import service as projects
from app.modules.projects.checklist import Found, Item
from app.modules.projects.models import ProjectLink
from app.packs.logistics import repository, service
from app.packs.logistics.models import Shipment, ShipmentState
from app.platform.items import ItemKind

CLIENT_ORDER: Final = "client_order"
CLIENT_INVOICE: Final = "client_invoice"
CLIENT_PAID: Final = "client_paid"
SUBCONTRACTOR_PAID: Final = "subcontractor_paid"
LOADING_PHOTO: Final = "loading_photo"
CMR: Final = "cmr"
PROOF_OF_DELIVERY: Final = "proof_of_delivery"
SUBCONTRACTOR_INVOICE: Final = "subcontractor_invoice"

ITEMS: Final = (
    # Many orders come by phone: asked only when the owner says so.
    Item(CLIENT_ORDER, per_truck=False, required=False),
    Item(CLIENT_INVOICE, per_truck=False),
    Item(CLIENT_PAID, per_truck=False),
    Item(SUBCONTRACTOR_PAID, per_truck=False),
    Item(LOADING_PHOTO, per_truck=True, required=False),
    # A domestic job has none: the owner marks it not needed.
    Item(CMR, per_truck=True),
    Item(PROOF_OF_DELIVERY, per_truck=True, done_by_mark=False),
    Item(SUBCONTRACTOR_INVOICE, per_truck=True),
)

# The papers that may become a truck's proof once the owner marks them.
_PROOF_PAPERS: Final = frozenset({"cmr", "pod_photo"})
_SUBCONTRACTOR_KINDS: Final = frozenset({InvoiceKind.PURCHASE, InvoiceKind.INCOMING_E_INVOICE})


async def read(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> list[Found]:
    trucks = [
        truck
        for truck in await repository.list_shipments(db, company_id, project_ids)
        if truck.state is not ShipmentState.REJECTED
    ]
    links = await projects.links_of_projects(db, company_id, project_ids)
    types = {
        file.id: file.doc_type
        for file in (
            await documents.get_documents(db, company_id, _items(links, ItemKind.DOCUMENT))
        ).values()
    }
    bills = await _bills(db, company_id, _items(links, ItemKind.INVOICE))
    # A bill's plates, and those of the e-invoice it was booked from.
    record_of = await books.booked_from(db, company_id, list(bills))
    record_of.update({bill_id: bill_id for bill_id in bills})
    plates: dict[uuid.UUID, set[str]] = {}
    for fact in await facts.facts_of_items(
        db, company_id=company_id, item_kind=ItemKind.INVOICE, item_ids=list(record_of)
    ):
        if fact.kind == "plate":
            plates.setdefault(record_of[fact.item_id], set()).add(fact.normalised)
    proofs = await service.proofs(db, company_id, project_ids)

    found: list[Found] = []
    for project_id in project_ids:
        own = [link for link in links if link.project_id == project_id]
        papers = [link for link in own if link.item_kind is ItemKind.DOCUMENT]
        billed = [
            link for link in own if link.item_kind is ItemKind.INVOICE and link.item_id in bills
        ]
        client = [link for link in billed if _is_client(bills[link.item_id])]
        carrier = [link for link in billed if _is_carrier(bills[link.item_id])]
        found += [
            _by_links(project_id, None, CLIENT_ORDER, _typed(papers, types, {"transport_order"})),
            _by_links(project_id, None, CLIENT_INVOICE, client),
            _paid(project_id, CLIENT_PAID, client, bills),
            _paid(project_id, SUBCONTRACTOR_PAID, carrier, bills),
        ]
        for truck in (truck for truck in trucks if truck.project_id == project_id):
            on_truck = [link for link in papers if link.shipment_id == truck.id]
            its_bills = [
                link
                for link in carrier
                if link.shipment_id == truck.id
                or (
                    link.shipment_id is None
                    and plates.get(link.item_id, set()) & service.compared_plates(truck)
                )
            ]
            found += [
                _by_links(
                    project_id, truck.id, LOADING_PHOTO, _typed(on_truck, types, {"loading_photo"})
                ),
                _by_links(project_id, truck.id, CMR, _typed(on_truck, types, {"cmr"})),
                _proof(truck, on_truck, types, proofs),
                _by_links(project_id, truck.id, SUBCONTRACTOR_INVOICE, its_bills),
            ]
    return found


def _items(links: Iterable[ProjectLink], kind: ItemKind) -> list[uuid.UUID]:
    return [link.item_id for link in links if link.item_kind is kind]


async def _bills(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, InvoiceOut]:
    """The project's bills, not cancelled, each with the purchase it was booked as when it is an
    e-invoice: what is paid is read off the booking."""
    found = {
        bill.id: bill
        for bill in await books.get_invoices(db, company_id, ids)
        if not bill.cancelled
    }
    booked = [bill.same_as_id for bill in found.values() if bill.same_as_id is not None]
    for bill in await books.get_invoices(db, company_id, booked):
        found.setdefault(bill.id, bill)
    return found


def _is_client(bill: InvoiceOut) -> bool:
    return bill.direction is InvoiceDirection.RECEIVABLE and bill.kind is InvoiceKind.SALES


def _is_carrier(bill: InvoiceOut) -> bool:
    return bill.direction is InvoiceDirection.PAYABLE and bill.kind in _SUBCONTRACTOR_KINDS


def _typed(
    links: Iterable[ProjectLink], types: dict[uuid.UUID, str | None], wanted: set[str]
) -> list[ProjectLink]:
    return [link for link in links if types.get(link.item_id) in wanted]


def _by_links(
    project_id: uuid.UUID, shipment_id: uuid.UUID | None, key: str, links: Sequence[ProjectLink]
) -> Found:
    counted = [link.id for link in links if projects.is_evidence(link)]
    if counted:
        return Found(project_id, shipment_id, key, "done", tuple(counted))
    if links:
        return Found(project_id, shipment_id, key, "waiting", tuple(link.id for link in links))
    return Found(project_id, shipment_id, key, "missing")


def _paid(
    project_id: uuid.UUID,
    key: str,
    links: Sequence[ProjectLink],
    bills: dict[uuid.UUID, InvoiceOut],
) -> Found:
    """Done when every bill a decision may rest on has nothing left to pay; waiting on what is
    still owed, or on the owner while the bills are only suggested."""
    counted = [link for link in links if projects.is_evidence(link)]
    if not counted:
        return _by_links(project_id, None, key, links)
    owed = [link.id for link in counted if not _settled(bills, bills[link.item_id])]
    if owed:
        return Found(project_id, None, key, "waiting", tuple(owed))
    return Found(project_id, None, key, "done", tuple(link.id for link in counted))


def _settled(bills: dict[uuid.UUID, InvoiceOut], bill: InvoiceOut) -> bool:
    if bill.same_as_id is not None and bill.same_as_id in bills:
        bill = bills[bill.same_as_id]
    return bill.remaining_amount is not None and bill.remaining_amount <= Decimal(0)


def _proof(
    truck: Shipment,
    on_truck: Sequence[ProjectLink],
    types: dict[uuid.UUID, str | None],
    proofs: dict[uuid.UUID, list[uuid.UUID]],
) -> Found:
    if truck.id in proofs:
        return Found(truck.project_id, truck.id, PROOF_OF_DELIVERY, "done", tuple(proofs[truck.id]))
    # On the truck and waiting for the owner's one tap.
    papers = [
        link.id
        for link in on_truck
        if link.proof_of_delivery or types.get(link.item_id) in _PROOF_PAPERS
    ]
    if papers:
        return Found(truck.project_id, truck.id, PROOF_OF_DELIVERY, "waiting", tuple(papers))
    return Found(truck.project_id, truck.id, PROOF_OF_DELIVERY, "missing")
