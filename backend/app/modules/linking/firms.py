"""A project's client and subcontractors found in the books (docs/architecture/09). What names a
firm in a role is a signal, and there are three kinds, each from its own source:

- ``reading``: a conversation's reading names the firm in the role, as the job's client or
  subcontractor, or as the firm the conversation deals with (``party``, ``party_role``);
- ``sender``: a mail of the project came from an address the books, or the owner, give the firm,
  or from a domain only its addresses in the books use. A forwarded mail's sender only passed it
  on and says nothing, nor does the company's own mail. The firm's role is the one its
  conversation's reading gives it, else the one side of the books it stands on (we only sell to
  it: a client);
- ``invoice``: the firm's invoice is on the project by its number, or by the owner: a sale names
  the client, a purchase a subcontractor.

A firm two kinds name in a role is suggested and counts as the project's firm for what rests on it
(which bills a plate may place) until the owner answers; one only one kind names is asked of the
owner. The owner's word stands: a client the owner set takes no suggestion, and a refused one
never comes back. A sender, a name or a number alone decides nothing.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceKind
from app.modules.conversations import service as conversations
from app.modules.linking import repository
from app.modules.parties import service as parties
from app.modules.profile import service as profile
from app.modules.projects import service as projects
from app.modules.projects.models import LinkMethod, LinkStatus, ProjectLink, ProjectRole
from app.platform.items import ItemKind

_ROLES = {"client": ProjectRole.CLIENT, "subcontractor": ProjectRole.SUBCONTRACTOR}
_BILLED = {
    InvoiceKind.SALES: ProjectRole.CLIENT,
    InvoiceKind.PURCHASE: ProjectRole.SUBCONTRACTOR,
    InvoiceKind.INCOMING_E_INVOICE: ProjectRole.SUBCONTRACTOR,
}

type _Key = tuple[uuid.UUID, ProjectRole, uuid.UUID]


@dataclass(slots=True)
class Found:
    """What a run found: firms suggested, and of them, those two kinds of signal name."""

    suggested: int = 0
    agreed: int = 0
    written: int = 0
    # By project number: what was found in each role.
    by_project: dict[str, list[str]] = field(default_factory=dict)


async def suggest_firms(db: AsyncSession, company_id: uuid.UUID) -> Found:
    """Works out, for each project not rejected, the firms that signals name in each role, and
    writes them as code's suggestions (``projects.suggest_parties``). Does not commit."""
    open_projects = await projects.open_projects(db, company_id)
    ids = [project.id for project in open_projects]
    links = await projects.links_of_projects(db, company_id, ids)
    signals: dict[_Key, dict[str, list[dict[str, str]]]] = {}

    def add(key: _Key, kind: str, item_kind: ItemKind, item_id: uuid.UUID) -> None:
        items = signals.setdefault(key, {}).setdefault(kind, [])
        item = {"item_kind": item_kind.value, "item_id": str(item_id), "signal": kind}
        if item not in items:
            items.append(item)

    mails = [link for link in links if link.item_kind is ItemKind.MESSAGE]
    threads = await conversations.thread_ids_of(db, company_id, [link.item_id for link in mails])
    in_thread: dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]] = {}
    for link in mails:
        if link.item_id in threads:
            in_thread.setdefault((link.project_id, threads[link.item_id]), []).append(link.item_id)
    # Each conversation's firms in their roles, as its readings name them.
    said: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[ProjectRole, str]]] = {}
    for project_id, thread_id, reading in await repository.readings_by_thread(db, company_id, ids):
        said.setdefault((project_id, thread_id), []).extend(_named(reading))
    found: dict[str, list[Any]] = {}
    for (project_id, thread_id), roles in said.items():
        for role, name in roles:
            if name not in found:
                found[name] = await parties.named_like(db, company_id, name)
            for party in found[name]:
                for mail_id in in_thread.get((project_id, thread_id), [])[:1]:
                    add((project_id, role, party.id), "reading", ItemKind.MESSAGE, mail_id)

    await _senders(db, company_id, mails, threads, said, add)
    await _invoices(db, company_id, links, add)
    signals = await _one_per_firm(db, company_id, signals)

    result = Found()
    names = {project.id: project.number for project in open_projects}
    for project_id in ids:
        for role in _ROLES.values():
            these = {
                party_id: projects.Suggestion(
                    signals=frozenset(kinds),
                    evidence=tuple(item for items in kinds.values() for item in items),
                )
                for (pid, held, party_id), kinds in signals.items()
                if pid == project_id and held is role
            }
            result.written += await projects.suggest_parties(
                db, company_id, project_id, role, these
            )
            result.suggested += len(these)
            agreed = [party_id for party_id, each in these.items() if len(each.signals) >= 2]
            result.agreed += len(agreed)
            if these:
                result.by_project.setdefault(names[project_id], []).append(
                    f"{role.value}: {len(agreed)} agreed, {len(these) - len(agreed)} to ask"
                )
    return result


async def _one_per_firm(
    db: AsyncSession, company_id: uuid.UUID, signals: dict[_Key, dict[str, list[dict[str, str]]]]
) -> dict[_Key, dict[str, list[dict[str, str]]]]:
    """The signals of cards that share a tax id put together, under the card for their firm; and
    of a firm named in two roles on one project, only the role more kinds of signal name (both,
    for the owner to choose, where as many name each)."""
    firm = await parties.one_per_firm(db, company_id, list({key[2] for key in signals}))
    merged: dict[_Key, dict[str, list[dict[str, str]]]] = {}
    for (project_id, role, party_id), kinds in signals.items():
        into = merged.setdefault((project_id, role, firm[party_id]), {})
        for kind, items in kinds.items():
            held = into.setdefault(kind, [])
            held.extend(item for item in items if item not in held)
    most: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
    for (project_id, _, party_id), kinds in merged.items():
        most[project_id, party_id] = max(most.get((project_id, party_id), 0), len(kinds))
    return {key: kinds for key, kinds in merged.items() if len(kinds) == most[key[0], key[2]]}


def _named(reading: dict[str, Any]) -> list[tuple[ProjectRole, str]]:
    """The firms a reading names in a role: the job's client and subcontractor, and the firm the
    conversation deals with in the role the reading gives it."""
    named = [(role, str(reading[key])) for key, role in _ROLES.items() if reading.get(key)]
    party, role = reading.get("party"), _ROLES.get(str(reading.get("party_role") or ""))
    if party and role is not None:
        named.append((role, str(party)))
    return named


async def _senders(
    db: AsyncSession,
    company_id: uuid.UUID,
    mails: Sequence[ProjectLink],
    threads: dict[uuid.UUID, uuid.UUID],
    said: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[ProjectRole, str]]],
    add: Any,
) -> None:
    """The firms the project's mail came from, in the role their conversation's reading gives
    them, else the one side of the books they stand on."""
    ourselves = await profile.who_we_are(db, company_id)
    senders = await conversations.mail_senders(db, company_id, [link.item_id for link in mails])
    firms: dict[str, list[Any]] = {}
    sides: dict[uuid.UUID, frozenset[InvoiceKind]] = {}
    for link in mails:
        sender = senders.get(link.item_id)
        if (
            sender is None
            or not sender.inbound
            or sender.forwarded
            or ourselves.writes_from(sender.address)
        ):
            continue
        address = sender.address.lower()
        if address not in firms:
            firms[address] = await parties.writing_from(db, company_id, address)
            sides |= await books.kinds_of_parties(db, company_id, [p.id for p in firms[address]])
        if not firms[address]:
            continue
        thread_id = threads.get(link.item_id)
        roles_read = said.get((link.project_id, thread_id), []) if thread_id else []
        for party in firms[address]:
            roles = {role for role, name in roles_read if parties.same_firm(name, party.name)}
            if not roles:
                billed = {_BILLED[kind] for kind in sides.get(party.id, ()) if kind in _BILLED}
                roles = billed if len(billed) == 1 else set()
            for role in roles:
                add((link.project_id, role, party.id), "sender", ItemKind.MESSAGE, link.item_id)


async def _invoices(
    db: AsyncSession, company_id: uuid.UUID, links: Sequence[ProjectLink], add: Any
) -> None:
    """The firms whose invoice is on the project by its number or by the owner."""
    held = [
        link
        for link in links
        if link.item_kind is ItemKind.INVOICE
        and link.status is not LinkStatus.REJECTED
        and (
            link.method in (LinkMethod.KEY, LinkMethod.OWNER) or link.status is LinkStatus.CONFIRMED
        )
    ]
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, company_id, [link.item_id for link in held])
    }
    for link in held:
        bill = bills.get(link.item_id)
        if bill is None or bill.party_id is None or bill.kind not in _BILLED:
            continue
        add(
            (link.project_id, _BILLED[bill.kind], bill.party_id),
            "invoice",
            ItemKind.INVOICE,
            bill.id,
        )
