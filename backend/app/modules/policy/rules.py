"""The hard rules (ADR-0002): pure functions over a payable's picture, each with a stable id that
is recorded with its result. No model's output reaches them: the picture is built by code from the
books, the confirmed links and the chain, and a rule reads nothing else.

A rule that fails says what it would take; the worst result decides: ``reject`` over ``hold``
over ``propose`` over ``ok``. What the owner decides beyond them is the decision's state
(``decisions``), never a rule's.
"""

from collections.abc import Callable
from typing import Final

from app.modules.policy.models import PaymentsRecorded
from app.modules.policy.schemas import Outcome, Picture, PolicyOut, Result
from app.platform import chain

_WEIGHT: Final = {Outcome.OK: 0, Outcome.PROPOSE: 1, Outcome.HOLD: 2, Outcome.REJECT: 3}


Rule = Callable[[Picture, PolicyOut], Result]


def _currency(picture: Picture, policy: PolicyOut) -> Result:
    if picture.usdc_units is None:
        return Result(
            "currency",
            Outcome.HOLD,
            f"No mirror rate for {picture.currency or 'a bill with no currency'}: it is never "
            "paid at a guessed rate",
        )
    return Result("currency", Outcome.OK)


def _amount(picture: Picture, policy: PolicyOut) -> Result:
    if picture.cancelled:
        return Result("amount", Outcome.HOLD, "The bill is cancelled in the books")
    if picture.left is None:
        return Result("amount", Outcome.HOLD, "The books give no amount left to pay on it")
    if picture.left <= 0:
        return Result("amount", Outcome.HOLD, "Nothing is left to pay on it in the books")
    if picture.amount <= 0 or picture.amount > picture.left:
        return Result("amount", Outcome.HOLD, "The amount is more than is left to pay on it")
    if picture.usdc_units == 0:
        return Result("amount", Outcome.HOLD, "Too small to be paid in test USDC")
    return Result("amount", Outcome.OK)


def _on_account(picture: Picture, policy: PolicyOut) -> Result:
    """The firm's account caps what is paid whatever the policy's setting (ADR-0022): a bill the
    account shows paid is never proposed, nor an amount above what it shows owed; one it shows
    partly owed is proposed for that much, the owner's call."""
    owed, on = picture.owed_on_account, picture.account_on
    of = f" on {on.isoformat()}" if on else ""
    if owed is not None and owed <= 0:
        return Result(
            "on_account", Outcome.HOLD, f"The firm's account in the books{of} shows it paid"
        )
    figure = f"{owed} {picture.currency}" if picture.currency else str(owed)
    if owed is not None and picture.amount > owed:
        return Result(
            "on_account",
            Outcome.HOLD,
            f"The firm's account in the books{of} shows only {figure} owed on it, less than the "
            "amount",
        )
    if owed is not None and picture.left is not None and owed < picture.left:
        return Result(
            "on_account",
            Outcome.PROPOSE,
            f"The firm's account in the books{of} shows {figure} owed on it, less than the bill",
        )
    if picture.within_tolerance:
        return Result(
            "on_account",
            Outcome.PROPOSE,
            "What is left is within the policy's tolerance: a bank fee or a rate difference?",
        )
    if owed is None and policy.payments_recorded is PaymentsRecorded.ON_ACCOUNT:
        return Result(
            "on_account", Outcome.PROPOSE, "The firm's account in the books cannot speak for it"
        )
    return Result("on_account", Outcome.OK)


def _duplicate(picture: Picture, policy: PolicyOut) -> Result:
    if picture.paid_on_chain:
        return Result("duplicate", Outcome.REJECT, "The vault has already paid this bill")
    if picture.paid_as_another:
        return Result(
            "duplicate",
            Outcome.REJECT,
            "Another bill with the same number or e-document id was approved or paid",
        )
    return Result("duplicate", Outcome.OK)


def _project(picture: Picture, policy: PolicyOut) -> Result:
    if picture.projects == 0:
        return Result("project", Outcome.HOLD, "The bill is in no confirmed job")
    return Result("project", Outcome.OK)


def _proof_of_delivery(picture: Picture, policy: PolicyOut) -> Result:
    if picture.trucks is None:
        return Result("proof_of_delivery", Outcome.OK)
    if not picture.trucks:
        return Result(
            "proof_of_delivery", Outcome.HOLD, "The bill names no truck of its job, so no delivery"
        )
    missing = picture.trucks.count(False)
    if missing:
        return Result(
            "proof_of_delivery",
            Outcome.HOLD,
            f"No proof of delivery the owner confirmed for {missing} of its "
            f"{len(picture.trucks)} trucks",
        )
    return Result("proof_of_delivery", Outcome.OK)


def _client_paid(picture: Picture, policy: PolicyOut) -> Result:
    if not policy.pay_when_paid:
        return Result("client_paid", Outcome.OK)
    if picture.client_invoices == 0:
        return Result("client_paid", Outcome.HOLD, "The job has no client invoice yet")
    if picture.client_unpaid:
        return Result("client_paid", Outcome.HOLD, "The client has not paid the job")
    return Result("client_paid", Outcome.OK)


def _vault_account(picture: Picture, policy: PolicyOut) -> Result:
    if not picture.party:
        return Result("vault_account", Outcome.HOLD, "The books name nobody to pay for it")
    if picture.vault is None:
        return Result("vault_account", Outcome.HOLD, "The company is not on the vault yet")
    if not picture.payee:
        return Result("vault_account", Outcome.HOLD, "The subcontractor has no wallet on the vault")
    return Result("vault_account", Outcome.OK)


def _timelock(picture: Picture, policy: PolicyOut) -> Result:
    if picture.vault is None or not picture.payee:
        return Result("timelock", Outcome.OK)
    if picture.payee_active_from is None:
        return Result(
            "timelock", Outcome.HOLD, "The vault does not list the subcontractor's wallet"
        )
    if picture.payee_active_from > picture.now:
        return Result(
            "timelock", Outcome.HOLD, "The subcontractor's wallet is new and within its timelock"
        )
    return Result("timelock", Outcome.OK)


def _caps(picture: Picture, policy: PolicyOut) -> Result:
    vault, units = picture.vault, picture.usdc_units
    if vault is None or units is None:
        return Result("vault_caps", Outcome.OK)
    if units > vault.per_payment_cap:
        return Result(
            "vault_caps",
            Outcome.HOLD,
            f"{chain.usdc(units)} test USDC is above the vault's cap of "
            f"{chain.usdc(vault.per_payment_cap)} a payment",
        )
    if units > vault.left_today:
        return Result(
            "vault_caps",
            Outcome.HOLD,
            f"Only {chain.usdc(vault.left_today)} test USDC is left of the vault's daily cap",
        )
    if units > vault.balance:
        return Result(
            "vault_caps",
            Outcome.HOLD,
            f"The vault holds {chain.usdc(vault.balance)} test USDC, less than the payment",
        )
    return Result("vault_caps", Outcome.OK)


def _approval_limit(picture: Picture, policy: PolicyOut) -> Result:
    amount = picture.in_policy_currency
    if amount is None or amount > policy.approval_limit:
        return Result("approval_limit", Outcome.PROPOSE, "Above the owner's approval limit")
    return Result("approval_limit", Outcome.OK)


RULES: Final[tuple[Rule, ...]] = (
    _currency,
    _amount,
    _on_account,
    _duplicate,
    _project,
    _proof_of_delivery,
    _client_paid,
    _vault_account,
    _timelock,
    _caps,
    _approval_limit,
)


def evaluate(picture: Picture, policy: PolicyOut) -> list[Result]:
    """Every rule's result, in the order above."""
    return [rule(picture, policy) for rule in RULES]


def verdict(results: list[Result]) -> Outcome:
    return max((result.outcome for result in results), key=_WEIGHT.__getitem__)
