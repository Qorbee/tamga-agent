from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DomainSource(StrEnum):
    """What shows a mail domain is the company's."""

    # A mailbox Tamga reads is on it: signing in to it proved the account.
    MAILBOX = "mailbox"
    # The company's own sent mail comes from it.
    SENT_MAIL = "sent_mail"
    OWNER = "owner"


class OwnDomain(BaseModel):
    domain: str
    # Whether Tamga takes it as the company's: mail from it is the company's own.
    ours: bool
    sources: list[DomainSource]
    # Distinct mails the company sent from it, and received from it.
    sent: int
    received: int
    # False for a mailbox's domain, which is the company's however the owner answers.
    removable: bool


class RegistryStatus(StrEnum):
    # The tax registry named the number's holder.
    FOUND = "found"
    # The registry has no e-document user by that number, which does not make it wrong.
    NOT_FOUND = "not_found"
    # The registry could not be asked.
    NOT_ASKED = "not_asked"


class TaxIdEvidence(BaseModel):
    """Where a tax number was seen in the company's own records."""

    papers: int
    sent_mails: int
    received_mails: int


class OwnTaxId(BaseModel):
    tax_id: str
    registry: RegistryStatus
    registry_title: str | None
    checked_at: datetime | None
    evidence: TaxIdEvidence


class TaxIdProposal(BaseModel):
    """A tax number the company's own sent mail gives, which no firm in its books has: Tamga's
    guess at the company's own, for the owner to confirm."""

    tax_id: str
    evidence: TaxIdEvidence


class CompanyProfile(BaseModel):
    """Who the company is: the name it goes by, its legal name and tax number, and the mail
    domains it writes from, each with what shows it."""

    name: str
    legal_name: str | None
    tax_id: OwnTaxId | None
    proposal: TaxIdProposal | None
    domains: list[OwnDomain]


class CheckVerdict(StrEnum):
    # Nothing contradicts it.
    OK = "ok"
    # Something casts doubt on it: it is saved only when the owner confirms.
    CONFIRM = "confirm"
    # It is someone else's, or can never name the company: it is never saved.
    REFUSED = "refused"
    # It is not a tax number, or not a domain.
    INVALID = "invalid"


class CheckReason(StrEnum):
    NOT_VALID = "not_valid"
    # A public provider's domain: anyone may write from it.
    SHARED_DOMAIN = "shared_domain"
    # A firm in the books has an address at the domain.
    FIRMS_ADDRESS = "firms_address"
    # A firm in the books has the tax number.
    FIRMS_TAX_ID = "firms_tax_id"
    # A firm's name starts with the name the domain was registered as.
    FIRMS_NAME = "firms_name"
    # Mail from the domain only comes in; the company never sent from it.
    ONLY_RECEIVED = "only_received"
    # No mail from the domain, sent or received.
    NO_MAIL = "no_mail"
    # Written in another script; its plain-letter spelling (``suggestion``) is the one in use.
    SPELLING = "spelling"
    # The tax registry names a holder (``title``) that is not the company by its name or domains.
    REGISTRY_NAME = "registry_name"
    # Neither the registry nor the company's mail and files know the number.
    NOT_SEEN = "not_seen"


class CheckFinding(BaseModel):
    reason: CheckReason
    firm: str | None = None
    title: str | None = None
    count: int | None = None
    suggestion: str | None = None


class IdentityCheck(BaseModel):
    # As it is stored; None when it is not valid.
    value: str | None
    verdict: CheckVerdict
    findings: list[CheckFinding]


class IdentityChange(BaseModel):
    saved: bool
    check: IdentityCheck
    profile: CompanyProfile


class TaxIdSet(BaseModel):
    tax_id: str = Field(min_length=1, max_length=40)
    # Kept only when the tax registry gives none.
    legal_name: str | None = Field(default=None, max_length=300)
    # The owner has read the check's doubts and says it is the company's all the same.
    confirm: bool = False


class DomainAdd(BaseModel):
    domain: str = Field(min_length=1, max_length=300)
    confirm: bool = False
