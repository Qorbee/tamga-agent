"""The hard rules (ADR-0002): pure functions over a payable's picture, each with a stable id that
is recorded with its result. No model's output reaches them: the picture is built by code from the
books, the confirmed links and the chain, and a rule reads nothing else.

A rule that fails says what it would take; the worst result decides: ``reject`` over ``hold``
over ``propose`` over ``ok``. What the owner decides beyond them is the decision's state
(``decisions``), never a rule's.

The core rules run for every way of paying; each payment method adds its own (ADR-0027): what is
about test USDC runs only for the vault, and a bank transfer needs an IBAN.
"""

from collections.abc import Callable, Mapping
from typing import Final

from app.modules.policy.models import PaymentMethod, PaymentsRecorded
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
    return Result("amount", Outcome.OK)


def _usdc_minimum(picture: Picture, policy: PolicyOut) -> Result:
    if picture.usdc_units == 0:
        return Result("usdc_minimum", Outcome.HOLD, "Too small to be paid in test USDC")
    return Result("usdc_minimum", Outcome.OK)


def _returned(picture: Picture, policy: PolicyOut) -> Result:
    if picture.returns:
        return Result("returned", Outcome.HOLD, "This is a return, not a bill to pay")
    return Result("returned", Outcome.OK)


def _read_as_return(picture: Picture, policy: PolicyOut) -> Result:
    if picture.read_as_return:
        return Result(
            "read_as_return",
            Outcome.PROPOSE,
            "Its file reads as a return or a credit note: look before paying",
        )
    return Result("read_as_return", Outcome.OK)


def _party(picture: Picture, policy: PolicyOut) -> Result:
    if not picture.party:
        return Result("party", Outcome.HOLD, "The books name nobody to pay for it")
    return Result("party", Outcome.OK)


def _method(picture: Picture, policy: PolicyOut) -> Result:
    if picture.method not in policy.methods:
        return Result(
            "method", Outcome.HOLD, "The way it is to be paid is turned off in the settings"
        )
    return Result("method", Outcome.OK)


def _reserve(picture: Picture, policy: PolicyOut) -> Result:
    """After this payment, the accounts keep what the reserve rule asks: the bills due within its
    weeks, the approved ones, and its floor. The figures are left out of the reason, so a balance
    that moves changes a decision only when it crosses the line."""
    rule = policy.reserve
    if not rule.active:
        return Result("reserve", Outcome.OK)
    if picture.balance is None:
        return Result(
            "reserve",
            Outcome.PROPOSE,
            "No bank balance is known, so the reserve cannot be checked",
        )
    if picture.committed is None or picture.in_policy_currency is None:
        return Result(
            "reserve", Outcome.PROPOSE, "A bill with no reference rate: the reserve is not checked"
        )
    left = picture.balance - picture.committed - picture.in_policy_currency
    if left >= rule.floor:
        return Result("reserve", Outcome.OK)
    kept = (
        f"the bills due in the next {rule.cover_weeks} weeks"
        if rule.cover_weeks
        else "nothing below zero"
    )
    return Result(
        "reserve",
        Outcome.HOLD if rule.on_breach == "hold" else Outcome.PROPOSE,
        f"Paying it would leave less than the reserve keeps ({kept}"
        + (f", and {rule.floor} {policy.currency}" if rule.floor else "")
        + ")",
    )


def _iban(picture: Picture, policy: PolicyOut) -> Result:
    """The owner pays a bank transfer from their own bank, where the firm's account is kept, so a
    missing IBAN is the owner's to look at, never a hold: what settles the bill is the owner's
    word, or a movement matched by the firm's tax number too."""
    if not picture.iban:
        return Result(
            "iban", Outcome.PROPOSE, "The books give no IBAN for the firm: pay the account you know"
        )
    return Result("iban", Outcome.OK)


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
    if amount is None:
        # Never compared at a guessed rate (ADR-0028).
        return Result(
            "approval_limit",
            Outcome.HOLD,
            "No reference rate to compare it with the owner's approval limit",
        )
    if amount > policy.approval_limit:
        return Result("approval_limit", Outcome.PROPOSE, "Above the owner's approval limit")
    return Result("approval_limit", Outcome.OK)


# Every way of paying.
RULES: Final[tuple[Rule, ...]] = (
    _amount,
    _returned,
    _read_as_return,
    _on_account,
    _duplicate,
    _party,
    _project,
    _proof_of_delivery,
    _client_paid,
    _method,
    _reserve,
    _approval_limit,
)
# What each way of paying adds; a new method is a new entry, the decisions unchanged.
METHOD_RULES: Final[Mapping[PaymentMethod, tuple[Rule, ...]]] = {
    PaymentMethod.VAULT_USDC: (_currency, _usdc_minimum, _vault_account, _timelock, _caps),
    PaymentMethod.BANK_TRANSFER: (_iban,),
}


def evaluate(picture: Picture, policy: PolicyOut) -> list[Result]:
    """Every rule's result: the core rules, then the method's, in the orders above."""
    return [rule(picture, policy) for rule in (*RULES, *METHOD_RULES[picture.method])]


def verdict(results: list[Result]) -> Outcome:
    return max((result.outcome for result in results), key=_WEIGHT.__getitem__)
