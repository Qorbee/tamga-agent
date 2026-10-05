"""Who the company is: the name it goes by, its legal name, its tax number and the mail domains it
writes from (docs/architecture/09).

Linking has to know the company before it can say who anyone else is: mail from the company's own
domain is its own, and its own tax number on a paper names no firm. A wrong value here would
mislead every link quietly for months, so none is taken on trust:

- a domain is the company's when a mailbox Tamga reads is on it, or the company's own sent mail
  comes from it; the owner may add one that the mail does not show, or say one is not theirs;
- a tax number is checked by its country's check digits, against the firms in the books, against
  where the company's mail and files show it, and against the tax registry, which gives the
  legal name;
- what shows a value is someone else's refuses it; what only casts doubt on it asks the owner to
  confirm, with the doubt in front of them.
"""

import re
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
from app.modules.facts import service as facts
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind
from app.modules.parties.service import Identifier
from app.modules.profile.schemas import (
    CheckFinding,
    CheckReason,
    CheckVerdict,
    CompanyProfile,
    DomainAdd,
    DomainSource,
    IdentityChange,
    IdentityCheck,
    OwnDomain,
    OwnTaxId,
    RegistryStatus,
    TaxIdEvidence,
    TaxIdProposal,
    TaxIdSet,
)
from app.platform import maildomains
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.errors import Conflict, NotFound
from app.platform.items import ItemKind

_TAX_ID: Final = IdentifierKind.TAX_ID.value
# How many tax numbers read from the company's mail the proposal weighs, newest first, and how
# many distinct numbers among them it looks at, those written most first.
_MAIL_FACTS: Final = 2000
_CANDIDATES: Final = 20
# A name shorter than this starts too many others to say two names are one firm's.
_SHORTEST_NAME: Final = 4

_tax_number_checks: dict[str, Callable[[str], bool]] = {}


def register_tax_number_check(country: str, check: Callable[[str], bool]) -> None:
    """A pack's test of its country's tax numbers, by their check digits."""
    _tax_number_checks[country] = check


@dataclass(frozen=True, slots=True)
class Ourselves:
    """Who the company is, for code that must tell it from the firms it deals with."""

    name: str
    legal_name: str | None
    tax_id: str | None
    domains: frozenset[str]
    # Domains the owner said are not the company's, though its sent mail comes from them.
    not_ours: frozenset[str] = frozenset()

    def writes_from(self, address: str) -> bool:
        """Whether a mail address is the company's own: at one of its domains or a subdomain. The
        most specific domain decides, so a subdomain the owner said is not theirs stays apart
        from the domain above it."""
        domain = maildomains.address_domain(address)
        if domain is None:
            return False
        answers = [(own, True) for own in self.domains] + [(own, False) for own in self.not_ours]
        held = [(own, ours) for own, ours in answers if maildomains.within(domain, own)]
        return bool(held) and max(held, key=lambda answer: len(answer[0]))[1]


async def who_we_are(db: AsyncSession, company_id: uuid.UUID) -> Ourselves:
    company = await _company(db, company_id)
    domains = await _domains(db, company_id)
    return Ourselves(
        name=company.name,
        legal_name=company.legal_name,
        tax_id=company.tax_id,
        domains=frozenset(own.domain for own in domains if own.ours),
        not_ours=frozenset(own.domain for own in domains if not own.ours),
    )


async def get_profile(db: AsyncSession, actor: Actor) -> CompanyProfile:
    """Who the company is. Only the owner reads the tax number whole: a sole trader's is their own
    identity number."""
    profile = await _profile(db, actor.company_id)
    if actor.background or actor.role is not Role.OWNER:
        if profile.tax_id is not None:
            masked = "•" * max(len(profile.tax_id.tax_id) - 2, 0) + profile.tax_id.tax_id[-2:]
            profile.tax_id = profile.tax_id.model_copy(update={"tax_id": masked})
        profile.proposal = None
    return profile


async def set_tax_id(db: AsyncSession, actor: Actor, body: TaxIdSet) -> IdentityChange:
    """The company's tax number, saved when the checks find nothing against it, or when they only
    doubt it and the owner confirms. The tax registry's name for it becomes the legal name."""
    actor.require(Role.OWNER)
    company = await _company(db, actor.company_id)
    check, registered, asked = await _check_tax_id(db, company, body.tax_id)
    saved = _passes(check, confirmed=body.confirm)
    if saved and check.value is not None:
        same = company.tax_id == check.value
        checked_at = utcnow() if asked else None
        if same and not asked:
            # The registry was not reached this time: its last answer on the number stands.
            registered, checked_at = company.tax_registry_title, company.tax_registry_checked_at
        typed = body.legal_name.strip() if body.legal_name else None
        await companies.set_tax_id(
            db,
            company.id,
            tax_id=check.value,
            legal_name=registered or typed or (company.legal_name if same else None),
            registry_title=registered,
            checked_at=checked_at,
        )
        await db.commit()
    return IdentityChange(saved=saved, check=check, profile=await _profile(db, actor.company_id))


async def add_domain(db: AsyncSession, actor: Actor, body: DomainAdd) -> IdentityChange:
    """A mail domain the owner says is the company's, saved as a tax number is."""
    actor.require(Role.OWNER)
    check, proven = await _check_domain(db, actor.company_id, body.domain)
    saved = _passes(check, confirmed=body.confirm)
    if saved and check.value is not None:
        if proven:
            # The mail shows it is the company's; an earlier "not ours" is taken back.
            await companies.forget_domain_word(db, actor.company_id, check.value)
        else:
            await companies.set_domain_word(db, actor.company_id, check.value, ours=True)
        await db.commit()
    return IdentityChange(saved=saved, check=check, profile=await _profile(db, actor.company_id))


async def remove_domain(db: AsyncSession, actor: Actor, written: str) -> CompanyProfile:
    """The owner says a domain is not the company's. A mailbox's domain stays: Tamga signs in to
    that mailbox as the company."""
    actor.require(Role.OWNER)
    domain = maildomains.domain_of(written)
    if domain is None:
        raise NotFound("No such domain")
    if domain in await conversations.mailbox_domains(db, actor.company_id):
        raise Conflict("A mailbox Tamga reads is on this domain")
    # Kept as an answer even with no mail from it yet, so mail found later does not undo it.
    await companies.set_domain_word(db, actor.company_id, domain, ours=False)
    await db.commit()
    return await _profile(db, actor.company_id)


def _passes(check: IdentityCheck, *, confirmed: bool) -> bool:
    return check.verdict is CheckVerdict.OK or (check.verdict is CheckVerdict.CONFIRM and confirmed)


async def _company(db: AsyncSession, company_id: uuid.UUID) -> Any:
    company = (await companies.get_companies(db, [company_id])).get(company_id)
    if company is None:
        raise NotFound("No such company")
    return company


async def _profile(db: AsyncSession, company_id: uuid.UUID) -> CompanyProfile:
    company = await _company(db, company_id)
    own_tax_id = None
    proposal = None
    if company.tax_id:
        if company.tax_registry_title:
            status = RegistryStatus.FOUND
        elif company.tax_registry_checked_at:
            status = RegistryStatus.NOT_FOUND
        else:
            status = RegistryStatus.NOT_ASKED
        own_tax_id = OwnTaxId(
            tax_id=company.tax_id,
            registry=status,
            registry_title=company.tax_registry_title,
            checked_at=company.tax_registry_checked_at,
            evidence=await _evidence(db, company_id, company.tax_id),
        )
    else:
        proposal = await _proposal(db, company)
    return CompanyProfile(
        name=company.name,
        legal_name=company.legal_name,
        tax_id=own_tax_id,
        proposal=proposal,
        domains=await _domains(db, company_id),
    )


# Domains


async def _domains(db: AsyncSession, company_id: uuid.UUID) -> list[OwnDomain]:
    """Every domain the mail shows is the company's, and every one the owner named, the
    company's first."""
    mailboxes = await conversations.mailbox_domains(db, company_id)
    sent_from = await conversations.mail_by_sender_domain(db, company_id)
    words = {row.domain: row.ours for row in await companies.domain_words(db, company_id)}
    named = sorted(
        domain
        for domain in mailboxes | set(sent_from) | set(words)
        if not maildomains.is_shared(domain)
    )
    mail = await conversations.mail_by_sender_domain(db, company_id, named) if named else {}
    found: list[OwnDomain] = []
    for domain in named:
        counts = mail.get(domain)
        sent = counts.sent if counts else 0
        received = counts.received if counts else 0
        word = words.get(domain)
        sources = [
            source
            for source, shows in (
                (DomainSource.MAILBOX, domain in mailboxes),
                (DomainSource.SENT_MAIL, sent > 0),
                (DomainSource.OWNER, word is True),
            )
            if shows
        ]
        ours = domain in mailboxes or (word if word is not None else sent > 0)
        found.append(
            OwnDomain(
                domain=domain,
                ours=ours,
                sources=sources,
                sent=sent,
                received=received,
                removable=domain not in mailboxes,
            )
        )
    found.sort(key=lambda own: (not own.ours, -own.sent, own.domain))
    return found


async def _check_domain(
    db: AsyncSession, company_id: uuid.UUID, written: str
) -> tuple[IdentityCheck, bool]:
    """The check of a domain the owner says is the company's, and whether the mail already shows
    it is."""
    domain = maildomains.domain_of(written)
    if domain is None:
        invalid = [CheckFinding(reason=CheckReason.NOT_VALID)]
        return _verdict(None, CheckVerdict.INVALID, invalid), False
    if maildomains.is_shared(domain):
        refused = [CheckFinding(reason=CheckReason.SHARED_DOMAIN)]
        return _verdict(domain, CheckVerdict.REFUSED, refused), False
    firms = await parties.firms_for_domain(db, company_id, domain)
    if firms.by_address:
        refused = [CheckFinding(reason=CheckReason.FIRMS_ADDRESS, firm=firms.by_address[0].name)]
        return _verdict(domain, CheckVerdict.REFUSED, refused), False
    plain = maildomains.folded(domain)
    mailboxes = await conversations.mailbox_domains(db, company_id)
    looked_up = [domain] if plain is None else [domain, plain]
    mail = await conversations.mail_by_sender_domain(db, company_id, looked_up)
    sent = mail[domain].sent if domain in mail else 0
    if domain in mailboxes or sent > 0:
        return _verdict(domain, CheckVerdict.OK, []), True
    doubts: list[CheckFinding] = []
    if plain is not None and (plain in mailboxes or plain in mail):
        doubts.append(CheckFinding(reason=CheckReason.SPELLING, suggestion=plain))
    if firms.by_name:
        doubts.append(CheckFinding(reason=CheckReason.FIRMS_NAME, firm=firms.by_name[0].name))
    received = mail[domain].received if domain in mail else 0
    if received:
        doubts.append(CheckFinding(reason=CheckReason.ONLY_RECEIVED, count=received))
    else:
        doubts.append(CheckFinding(reason=CheckReason.NO_MAIL))
    return _verdict(domain, CheckVerdict.CONFIRM, doubts), False


# Tax number


def _tax_number(country: str, written: str) -> str | None:
    """The tax number as it is compared, when it can be one; its country's check digits decide
    where a pack gives them."""
    value = re.sub(r"[^0-9A-Z]", "", written.upper())
    if not 5 <= len(value) <= 20:
        return None
    check = _tax_number_checks.get(country)
    return value if check is None or check(value) else None


async def _check_tax_id(
    db: AsyncSession, company: Any, written: str
) -> tuple[IdentityCheck, str | None, bool]:
    """The check of a tax number the owner says is the company's; with it, the name the tax
    registry gives for it, and whether the registry was reached."""
    tax_id = _tax_number(company.country, written)
    if tax_id is None:
        invalid = [CheckFinding(reason=CheckReason.NOT_VALID)]
        return _verdict(None, CheckVerdict.INVALID, invalid), None, False
    domains = (await who_we_are(db, company.id)).domains
    firms = await _firms_with(db, company, tax_id, domains)
    if firms:
        refused = [CheckFinding(reason=CheckReason.FIRMS_TAX_ID, firm=firms[0])]
        return _verdict(tax_id, CheckVerdict.REFUSED, refused), None, False
    try:
        holder = await books.look_up_taxpayer(db, company.id, tax_id)
        asked = True
    except books.RegistryUnavailable:
        holder, asked = None, False
    title = holder.title if holder is not None else None
    if not asked and company.tax_id == tax_id:
        title = company.tax_registry_title  # its last answer on this very number
    if title is not None:
        if _names_us(title, company.name, domains):
            return _verdict(tax_id, CheckVerdict.OK, []), title, asked
        doubt = [CheckFinding(reason=CheckReason.REGISTRY_NAME, title=title)]
        return _verdict(tax_id, CheckVerdict.CONFIRM, doubt), title, asked
    evidence = await _evidence(db, company.id, tax_id)
    if evidence.papers or evidence.sent_mails:
        return _verdict(tax_id, CheckVerdict.OK, []), None, asked
    doubt = [CheckFinding(reason=CheckReason.NOT_SEEN)]
    return _verdict(tax_id, CheckVerdict.CONFIRM, doubt), None, asked


async def _firms_with(
    db: AsyncSession, company: Any, tax_id: str, domains: frozenset[str]
) -> list[str]:
    """The firms in the books with this tax number. A card the company keeps for itself, named as
    it is, is not another firm's."""
    matches = await parties.find_parties(
        db, company_id=company.id, identifier=Identifier(IdentifierKind.TAX_ID, tax_id)
    )
    names = (match.party.name for match in matches)
    return [name for name in names if not _names_us(name, company.name, domains)]


def _names_us(title: str, name: str, domains: frozenset[str]) -> bool:
    """Whether a registered name is the company's: the name the company goes by, or one that
    starts with its first word or with the name one of its domains was registered as."""
    held = parties.name_key(title).replace(" ", "")
    if held and held == parties.name_key(name).replace(" ", ""):
        return True  # "ABC LTD" is "ABC", however short the name
    ours = parties.name_key(name).split()[:1]
    ours += [parties.name_key(maildomains.name_of(domain)) for domain in domains]
    return any(len(word) >= _SHORTEST_NAME and held.startswith(word) for word in ours)


async def _evidence(db: AsyncSession, company_id: uuid.UUID, tax_id: str) -> TaxIdEvidence:
    """Where the company's own records show a tax number: files, and mail it sent or received."""
    found = await facts.items_sharing(db, company_id=company_id, kind=_TAX_ID, normalised=tax_id)
    papers = {fact.item_id for fact in found if fact.item_kind == ItemKind.DOCUMENT}
    mails = {fact.item_id for fact in found if fact.item_kind == ItemKind.MESSAGE}
    sent = await conversations.outbound_among(db, company_id, list(mails))
    return TaxIdEvidence(papers=len(papers), sent_mails=len(sent), received_mails=len(mails - sent))


async def _proposal(db: AsyncSession, company: Any) -> TaxIdProposal | None:
    """The tax number the company's own sent mail gives most, when no firm in its books has it:
    a signature's "VKN: …". None when two are written as often, since a guess would mislead;
    files never break that tie, as a firm's number is on its own invoices too."""
    in_mail = await facts.facts_of_kind(
        db, company_id=company.id, kind=_TAX_ID, item_kind=ItemKind.MESSAGE, limit=_MAIL_FACTS
    )
    sent = await conversations.outbound_among(db, company.id, list({f.item_id for f in in_mail}))
    written = Counter(fact.normalised for fact in in_mail if fact.item_id in sent)
    domains = (await who_we_are(db, company.id)).domains if written else frozenset()
    # The two numbers written most that no firm holds decide: the first, unless the second was
    # written as often. A firm's number is passed over, up to a bound.
    found: list[tuple[str, int]] = []
    for tax_id, count in written.most_common(_CANDIDATES):
        if _tax_number(company.country, tax_id) != tax_id:
            continue
        if await _firms_with(db, company, tax_id, domains):
            continue
        found.append((tax_id, count))
        if len(found) == 2:
            break
    if not found or (len(found) == 2 and found[0][1] == found[1][1]):
        return None
    tax_id = found[0][0]
    return TaxIdProposal(tax_id=tax_id, evidence=await _evidence(db, company.id, tax_id))


def _verdict(
    value: str | None, verdict: CheckVerdict, findings: Sequence[CheckFinding]
) -> IdentityCheck:
    return IdentityCheck(value=value, verdict=verdict, findings=list(findings))
