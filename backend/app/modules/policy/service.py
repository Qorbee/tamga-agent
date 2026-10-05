"""A company's policy (ADR-0002): the owner's rules for paying, versioned, and the mirror that
turns a bill's amount into the test USDC it is paid as (ADR-0019). The rules themselves are pure
functions in ``rules.py``."""

import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.policy import repository, rules
from app.modules.policy.models import PaymentsRecorded, Policy
from app.modules.policy.schemas import (
    Outcome,
    Picture,
    PolicyChange,
    PolicyIn,
    PolicyOut,
    Result,
)
from app.platform import chain
from app.platform.auth import Actor, Role
from app.platform.errors import BadRequest

# Until the owner sets a policy: every payment is the owner's to approve, and a subcontractor is
# paid once the client paid.
DEFAULT: Final = PolicyOut(
    version=0,
    currency="USD",
    approval_limit=Decimal("0.00"),
    pay_when_paid=True,
    payments_recorded=PaymentsRecorded.ON_ACCOUNT,
    remainder_tolerance=Decimal("2.00"),
    created_at=None,
)
_CENT: Final = Decimal("0.01")
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
    return await _put_in_force(db, actor, body)


async def change_policy(db: AsyncSession, actor: Actor, body: PolicyChange) -> PolicyOut:
    """Puts in force a new version with the fields given changed from the version in force as it
    stands under the lock, so a change made elsewhere meanwhile is kept. Commits."""
    actor.require(Role.OWNER)
    await repository.lock_versions(db, actor.company_id)
    current = await current_policy(db, actor.company_id)
    merged = current.model_dump(include=set(PolicyIn.model_fields)) | body.model_dump(
        exclude_none=True
    )
    return await _put_in_force(db, actor, PolicyIn.model_validate(merged))


async def _put_in_force(db: AsyncSession, actor: Actor, body: PolicyIn) -> PolicyOut:
    """Under the caller's lock on the company's versions."""
    if body.currency not in mirror_rates():
        # Every amount of the policy is compared through this currency's rate.
        raise BadRequest(f"No mirror rate for {body.currency}")
    before = await repository.current(db, actor.company_id)
    policy = Policy(
        company_id=actor.company_id,
        version=1 if before is None else before.version + 1,
        currency=body.currency,
        approval_limit=body.approval_limit,
        pay_when_paid=body.pay_when_paid,
        payments_recorded=body.payments_recorded,
        remainder_tolerance=body.remainder_tolerance,
        created_by=actor.user_id,
    )
    await repository.add(db, policy)
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


def convert(amount: Decimal, currency: str | None, into: str) -> Decimal | None:
    """An amount in another currency, through the ratio of the two mirror rates, never figure
    against figure; None when either has no rate."""
    rates = mirror_rates()
    source, target = rates.get(currency or ""), rates.get(into)
    if source is None or target is None:
        return None
    return (amount * source / target).quantize(_CENT)


def evaluate(picture: Picture, policy: PolicyOut) -> tuple[Outcome, list[Result]]:
    """Every hard rule's result for a payable's picture, and the worst of them, which decides."""
    results = rules.evaluate(picture, policy)
    return rules.verdict(results), results
