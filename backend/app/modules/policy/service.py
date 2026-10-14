"""A company's policy (ADR-0002): the owner's rules for paying, versioned, and the mirror that
turns a bill's amount into the test USDC it is paid as (ADR-0019). The rules themselves are pure
functions in ``rules.py``."""

import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.fx import service as fx
from app.modules.policy import repository, reserve, rules
from app.modules.policy.models import (
    DraftState,
    PaymentMethod,
    PaymentsRecorded,
    Policy,
    PolicyDraft,
)
from app.modules.policy.reserve import ReserveRule
from app.modules.policy.schemas import (
    Outcome,
    Picture,
    PolicyChange,
    PolicyDraftOut,
    PolicyIn,
    PolicyOut,
    Result,
)
from app.platform import chain
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.errors import BadRequest, Conflict, NotFound

# Until the owner sets a policy: every payment is the owner's to approve, and a subcontractor is
# paid once the client paid.
DEFAULT: Final = PolicyOut(
    version=0,
    currency="USD",
    approval_limit=Decimal("0.00"),
    pay_when_paid=True,
    payments_recorded=PaymentsRecorded.PER_INVOICE,
    remainder_tolerance=Decimal("2.00"),
    methods=[PaymentMethod.VAULT_USDC],
    default_method=PaymentMethod.VAULT_USDC,
    reserve=reserve.OFF,
    reserve_rules=[],
    payment_weekday=None,
    created_at=None,
)
# The methods Tamga pays by itself, through the executor (ADR-0027); the others the owner pays,
# and Tamga records.
TAMGA_PAYS: Final = frozenset({PaymentMethod.VAULT_USDC})


def tamga_pays(method: PaymentMethod) -> bool:
    return method in TAMGA_PAYS


def reserve_presets() -> list[ReserveRule]:
    return list(reserve.PRESETS)


# Country packs' defaults, under the rates in settings.
_pack_rates: dict[str, Decimal] = {}


def register_mirror_rate(currency: str, rate: Decimal) -> None:
    """A country pack's default rate for its own currency (ADR-0019); ``MIRROR_RATES`` wins."""
    _pack_rates[currency] = rate


def mirror_rates() -> dict[str, Decimal]:
    return {**_pack_rates, **get_settings().mirror_rates}


async def current_policy(db: AsyncSession, company_id: uuid.UUID) -> PolicyOut:
    policy = await repository.current(db, company_id)
    return DEFAULT if policy is None else PolicyOut.model_validate(policy)


async def get_policy(db: AsyncSession, actor: Actor) -> PolicyOut:
    return await current_policy(db, actor.company_id)


async def set_policy(db: AsyncSession, actor: Actor, body: PolicyIn) -> PolicyOut:
    """Puts a new version in force. Decisions made under the old one are evaluated again on the
    next pass, since the version is part of what they were made from. Commits."""
    actor.require(Role.OWNER)
    await repository.lock_versions(db, actor.company_id)
    return await _put_in_force(db, actor.company_id, body, actor.user_id)


async def change_policy(db: AsyncSession, actor: Actor, body: PolicyChange) -> PolicyOut:
    """Puts in force a new version with the fields given changed from the version in force as it
    stands under the lock, so a change made elsewhere meanwhile is kept. Commits."""
    made = await _change(db, actor, body)
    await db.commit()
    return made


async def _change(db: AsyncSession, actor: Actor, body: PolicyChange) -> PolicyOut:
    """``change_policy`` without its commit, for a caller that writes more with it."""
    actor.require(Role.OWNER)
    await repository.lock_versions(db, actor.company_id)
    current = await current_policy(db, actor.company_id)
    body_in_force = _merged(current, body)
    return await _put_in_force(db, actor.company_id, body_in_force, actor.user_id, commit=False)


def _merged(current: PolicyOut, body: PolicyChange) -> PolicyIn:
    """The version in force with the change's fields put in it."""
    own = [rule for rule in current.reserve_rules if rule.key != body.remove_rule]
    if body.save_rule is not None:
        own = [rule for rule in own if rule.key != body.save_rule.key] + [body.save_rule]
    kept = current.model_dump(include=set(PolicyIn.model_fields)) | {
        "reserve_rule": current.reserve.key
    }
    merged = (
        kept
        | body.model_dump(exclude_none=True, exclude={"save_rule", "remove_rule"})
        | {"reserve_rules": [rule.model_dump() for rule in own]}
    )
    if merged.get("payment_weekday") == -1:
        merged["payment_weekday"] = None
    try:
        return PolicyIn.model_validate(merged)
    except ValidationError as error:
        # The change is valid alone, but not with what it keeps: the default method dropped
        # from the methods, say.
        raise BadRequest(error.errors()[0]["msg"]) from error


async def draft_change(
    db: AsyncSession,
    company_id: uuid.UUID,
    change: PolicyChange,
    *,
    said: str,
    chat_id: uuid.UUID | None,
) -> PolicyDraftOut:
    """Drafts a change from the owner's words, in force only once the owner confirms it
    (``confirm_draft``). Refused when it would not hold with the version in force, or changes
    nothing. Commits."""
    current = await current_policy(db, company_id)
    after = _merged(current, change)
    company = (await companies.get_companies(db, [company_id]))[company_id]
    if not fx.quotes(company.country, after.currency):
        raise BadRequest(f"No reference rate for {after.currency}")
    before = current.model_dump(include=set(PolicyIn.model_fields)) | {
        "reserve_rule": current.reserve.key
    }
    if after.model_dump() == PolicyIn.model_validate(before).model_dump():
        raise BadRequest("The policy in force already says this")
    draft = PolicyDraft(
        company_id=company_id,
        change=change.model_dump(mode="json", exclude_none=True),
        said=said[:500],
        based_on=current.version,
        chat_id=chat_id,
    )
    await repository.add_draft(db, draft)
    await db.commit()
    return PolicyDraftOut.model_validate(draft)


async def get_draft(db: AsyncSession, actor: Actor, draft_id: uuid.UUID) -> PolicyDraftOut:
    draft = await repository.get_draft(db, actor.company_id, draft_id)
    if draft is None:
        raise NotFound("No such draft")
    return PolicyDraftOut.model_validate(draft)


async def confirm_draft(db: AsyncSession, actor: Actor, draft_id: uuid.UUID) -> PolicyDraftOut:
    """Puts a drafted change in force over the version in force now: the owner's only, once.
    The draft and the new version are written together. Commits."""
    actor.require(Role.OWNER)
    draft = await _open_draft(db, actor, draft_id)
    draft.state = DraftState.CONFIRMED
    draft.decided_by = actor.user_id
    draft.decided_at = utcnow()
    made = await _change(db, actor, PolicyChange.model_validate(draft.change))
    draft.made_version = made.version
    await db.commit()
    return PolicyDraftOut.model_validate(draft)


async def discard_draft(db: AsyncSession, actor: Actor, draft_id: uuid.UUID) -> PolicyDraftOut:
    """Drops a drafted change: the owner's only. Commits."""
    actor.require(Role.OWNER)
    draft = await _open_draft(db, actor, draft_id)
    draft.state = DraftState.DISCARDED
    draft.decided_by = actor.user_id
    draft.decided_at = utcnow()
    await db.commit()
    return PolicyDraftOut.model_validate(draft)


async def _open_draft(db: AsyncSession, actor: Actor, draft_id: uuid.UUID) -> PolicyDraft:
    """The draft, locked; refused once answered, so a second tap never makes a second version."""
    draft = await repository.get_draft(db, actor.company_id, draft_id, for_update=True)
    if draft is None:
        raise NotFound("No such draft")
    if draft.state is not DraftState.DRAFT:
        raise Conflict(f"This draft was {draft.state.value} already")
    return draft


async def seed_policy(db: AsyncSession, company_id: uuid.UUID, body: PolicyIn) -> None:
    """A sandbox scenario's policy, as its owner would have set it, written by nobody. Only a
    sandbox, and only before any version: an owner's own policy is never replaced. Commits."""
    company = (await companies.get_companies(db, [company_id]))[company_id]
    if company.kind is not CompanyKind.SANDBOX:
        raise ValueError("only a sandbox scenario seeds a policy")
    await repository.lock_versions(db, company_id)
    if await repository.current(db, company_id) is None:
        await _put_in_force(db, company_id, body, None)


async def _put_in_force(
    db: AsyncSession,
    company_id: uuid.UUID,
    body: PolicyIn,
    created_by: uuid.UUID | None,
    *,
    commit: bool = True,
) -> PolicyOut:
    """Under the caller's lock on the company's versions."""
    company = (await companies.get_companies(db, [company_id]))[company_id]
    if not fx.quotes(company.country, body.currency):
        # Every amount of the policy is compared through this currency's reference rate.
        raise BadRequest(f"No reference rate for {body.currency}")
    before = await repository.current(db, company_id)
    in_force = reserve.resolve(body.reserve_rule, body.reserve_rules)
    if in_force is None:  # PolicyIn checks it; here for the type
        raise BadRequest("No such reserve rule")
    policy = Policy(
        company_id=company_id,
        version=1 if before is None else before.version + 1,
        currency=body.currency,
        approval_limit=body.approval_limit,
        pay_when_paid=body.pay_when_paid,
        payments_recorded=body.payments_recorded,
        remainder_tolerance=body.remainder_tolerance,
        methods=[method.value for method in body.methods],
        default_method=body.default_method,
        reserve=in_force.model_dump(mode="json"),
        reserve_rules=[rule.model_dump(mode="json") for rule in body.reserve_rules],
        payment_weekday=body.payment_weekday,
        created_by=created_by,
    )
    await repository.add(db, policy)
    if commit:
        await db.commit()
    return PolicyOut.model_validate(policy)


@dataclass(frozen=True, slots=True)
class Mirror:
    """A bill's amount as test USDC (ADR-0019): the rate used, and the units paid."""

    rate: Decimal
    units: int


def mirror(amount: Decimal, currency: str | None) -> Mirror | None:
    """The test USDC an amount is paid as, rounded down to USDC's six decimals; None for a
    currency with no rate."""
    rate = mirror_rates().get(currency or "")
    if rate is None:
        return None
    units = (amount * rate * chain.USDC_UNIT).to_integral_value(rounding=ROUND_DOWN)
    return Mirror(rate=rate, units=int(units))


def evaluate(picture: Picture, policy: PolicyOut) -> tuple[Outcome, list[Result]]:
    """Every hard rule's result for a payable's picture, and the worst of them, which decides."""
    results = rules.evaluate(picture, policy)
    return rules.verdict(results), results
