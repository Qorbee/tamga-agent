"""The hard rules, as pure functions over a picture: each says what fails and the worst decides."""

import dataclasses
from datetime import date
from decimal import Decimal

from app.modules.policy import reserve
from app.modules.policy import service as policies
from app.modules.policy.models import PaymentMethod, PaymentsRecorded
from app.modules.policy.schemas import Outcome, Picture, PolicyOut, Vault

POLICY = policies.DEFAULT

CLEAR = Picture(
    amount=Decimal("1900.00"),
    left=Decimal("1900.00"),
    owed_on_account=Decimal("1900.00"),
    account_on=date(2026, 10, 3),
    within_tolerance=False,
    cancelled=False,
    party=True,
    currency="EUR",
    usdc_units=220_400,
    in_policy_currency=Decimal("2204.00"),
    projects=1,
    trucks=(True,),
    client_invoices=1,
    client_unpaid=0,
    paid_on_chain=False,
    paid_as_another=False,
    vault=Vault(balance=500_000, per_payment_cap=300_000, left_today=500_000),
    payee=True,
    payee_active_from=100,
    now=200,
)


def failed(picture: Picture) -> dict[str, Outcome]:
    outcome, results = policies.evaluate(picture, POLICY)
    failing: dict[str, Outcome] = {
        result.rule: result.outcome for result in results if result.outcome is not Outcome.OK
    }
    assert outcome is max(
        failing.values(),
        key=[Outcome.OK, Outcome.PROPOSE, Outcome.HOLD, Outcome.REJECT].index,
        default=Outcome.OK,
    )
    return failing


def test_a_bill_every_rule_allows_is_still_the_owners_to_approve() -> None:
    assert failed(CLEAR) == {"approval_limit": Outcome.PROPOSE}
    under = POLICY.model_copy(update={"approval_limit": Decimal("5000.00")})
    _, results = policies.evaluate(CLEAR, under)
    assert all(result.outcome is Outcome.OK for result in results)


def test_each_truck_needs_its_proof_and_a_bill_with_none_named_waits() -> None:
    assert failed(dataclasses.replace(CLEAR, trucks=(True, False)))["proof_of_delivery"] == (
        Outcome.HOLD
    )
    assert failed(dataclasses.replace(CLEAR, trucks=()))["proof_of_delivery"] == Outcome.HOLD
    # No pack says what delivering means: the rule has nothing to ask.
    assert "proof_of_delivery" not in failed(dataclasses.replace(CLEAR, trucks=None))


def test_pay_when_paid_holds_until_the_client_paid_the_job() -> None:
    assert failed(dataclasses.replace(CLEAR, client_unpaid=1))["client_paid"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, client_invoices=0))["client_paid"] == Outcome.HOLD
    off = POLICY.model_copy(update={"pay_when_paid": False})
    _, results = policies.evaluate(dataclasses.replace(CLEAR, client_unpaid=1), off)
    assert {r.rule: r.outcome for r in results}["client_paid"] is Outcome.OK


def test_a_bill_paid_before_is_rejected_whatever_else_holds() -> None:
    picture = dataclasses.replace(CLEAR, paid_on_chain=True, trucks=(False,))
    outcome, _ = policies.evaluate(picture, POLICY)
    assert outcome is Outcome.REJECT
    assert failed(dataclasses.replace(CLEAR, paid_as_another=True))["duplicate"] == Outcome.REJECT


def test_the_vault_bounds_what_can_be_paid_at_all() -> None:
    vault = Vault(balance=500_000, per_payment_cap=200_000, left_today=500_000)
    assert failed(dataclasses.replace(CLEAR, vault=vault))["vault_caps"] == Outcome.HOLD
    vault = Vault(balance=500_000, per_payment_cap=300_000, left_today=100_000)
    assert failed(dataclasses.replace(CLEAR, vault=vault))["vault_caps"] == Outcome.HOLD
    vault = Vault(balance=100_000, per_payment_cap=300_000, left_today=500_000)
    assert failed(dataclasses.replace(CLEAR, vault=vault))["vault_caps"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, vault=None))["vault_account"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, payee=False))["vault_account"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, party=False))["party"] == Outcome.HOLD
    # A wallet listed a moment ago waits out its timelock.
    assert failed(dataclasses.replace(CLEAR, payee_active_from=300))["timelock"] == Outcome.HOLD


def test_an_amount_is_never_guessed() -> None:
    no_rate = dataclasses.replace(CLEAR, currency="XYZ", usdc_units=None, in_policy_currency=None)
    assert failed(no_rate)["currency"] == Outcome.HOLD
    # With no reference rate, the limit cannot be read, and nothing is guessed.
    assert failed(no_rate)["approval_limit"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, left=None))["amount"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, left=Decimal("0.00")))["amount"] == Outcome.HOLD
    more = dataclasses.replace(CLEAR, amount=Decimal("2000.00"))
    assert failed(more)["amount"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, cancelled=True))["amount"] == Outcome.HOLD


def test_a_bill_is_mirrored_at_its_currencys_rate_rounded_down() -> None:
    mirrored = policies.mirror(Decimal("1900.00"), "EUR")
    assert mirrored is not None and mirrored.units == 220_400
    assert policies.mirror(Decimal("1900.00"), "XYZ") is None


def test_the_firms_account_narrows_what_is_paid_whatever_the_setting() -> None:
    per_invoice = POLICY.model_copy(update={"payments_recorded": PaymentsRecorded.PER_INVOICE})
    paid = dataclasses.replace(CLEAR, owed_on_account=Decimal("0.00"))
    for policy in (POLICY, per_invoice):
        _, results = policies.evaluate(paid, policy)
        [held] = [result for result in results if result.rule == "on_account"]
        assert held.outcome is Outcome.HOLD and "2026-10-03" in held.reason
    # Partly owed: the smaller figure is proposed, and the owner decides.
    partly = dataclasses.replace(
        CLEAR, amount=Decimal("1200.00"), owed_on_account=Decimal("1200.00")
    )
    assert failed(partly)["on_account"] is Outcome.PROPOSE
    # An amount the owner raised above what the account says is owed is never paid.
    raised = dataclasses.replace(CLEAR, owed_on_account=Decimal("1200.00"))
    assert failed(raised)["on_account"] is Outcome.HOLD
    assert (
        failed(dataclasses.replace(CLEAR, within_tolerance=True))["on_account"] is Outcome.PROPOSE
    )


def test_an_account_that_cannot_speak_is_noted_only_where_payments_go_on_account() -> None:
    silent = dataclasses.replace(CLEAR, owed_on_account=None, account_on=None)
    on_account = POLICY.model_copy(update={"payments_recorded": PaymentsRecorded.ON_ACCOUNT})
    _, results = policies.evaluate(silent, on_account)
    assert [r.outcome for r in results if r.rule == "on_account"] == [Outcome.PROPOSE]
    per_invoice = POLICY.model_copy(update={"payments_recorded": PaymentsRecorded.PER_INVOICE})
    _, results = policies.evaluate(silent, per_invoice)
    assert [r.outcome for r in results if r.rule == "on_account"] == [Outcome.OK]


BANK = POLICY.model_copy(
    update={
        "methods": [PaymentMethod.VAULT_USDC, PaymentMethod.BANK_TRANSFER],
        "default_method": PaymentMethod.BANK_TRANSFER,
        "approval_limit": Decimal("5000.00"),
    }
)
BY_BANK = dataclasses.replace(
    CLEAR, method=PaymentMethod.BANK_TRANSFER, iban=True, usdc_units=None, vault=None, payee=False
)


def results(picture: Picture, policy: PolicyOut = BANK) -> dict[str, Outcome]:
    _, given = policies.evaluate(picture, policy)
    return {result.rule: result.outcome for result in given}


def test_the_vaults_rules_never_hold_a_bill_paid_by_bank() -> None:
    # No mirror rate, no vault, no wallet: none of it is asked of a bank transfer.
    ran = results(BY_BANK)
    assert set(ran).isdisjoint({"currency", "usdc_minimum", "vault_account", "timelock"})
    assert all(outcome is Outcome.OK for outcome in ran.values())
    # No IBAN in the books: the owner pays the account they know, and is told.
    assert results(dataclasses.replace(BY_BANK, iban=False))["iban"] is Outcome.PROPOSE
    # Turned off in the settings, a way to pay holds whatever it was chosen for.
    vault_only = BANK.model_copy(
        update={"methods": [PaymentMethod.VAULT_USDC], "default_method": PaymentMethod.VAULT_USDC}
    )
    assert results(BY_BANK, vault_only)["method"] is Outcome.HOLD
    # And a vault bill too small for test USDC is the vault's to hold, by its own rule.
    assert results(dataclasses.replace(CLEAR, usdc_units=0))["usdc_minimum"] is Outcome.HOLD


def test_the_reserve_keeps_the_bills_due_and_its_floor() -> None:
    steady = BANK.model_copy(update={"reserve": reserve.resolve("steady", [])})
    careful = BANK.model_copy(update={"reserve": reserve.resolve("careful", [])})
    # 2,204 of the policy's currency to pay, 10,000 in the accounts.
    rich = dataclasses.replace(BY_BANK, balance=Decimal("10000"), committed=Decimal("5000"))
    assert results(rich, steady)["reserve"] is Outcome.OK
    # What the other bills due need is kept: under it, Steady asks and Careful holds.
    short = dataclasses.replace(rich, committed=Decimal("9000"))
    assert results(short, steady)["reserve"] is Outcome.PROPOSE
    assert results(short, careful)["reserve"] is Outcome.HOLD
    # A floor of the owner's own, in a rule duplicated from a preset.
    own = reserve.ReserveRule.model_validate(
        reserve.resolve("steady", []).model_dump()  # type: ignore[union-attr]
        | {"key": "own:a1", "name": "Mine", "based_on": "steady", "floor": Decimal("3000")}
    )
    assert results(rich, steady.model_copy(update={"reserve": own}))["reserve"] is Outcome.PROPOSE
    # Not known is never taken as enough, nor as too little: the owner is asked.
    unknown = dataclasses.replace(BY_BANK, balance=None)
    assert results(unknown, careful)["reserve"] is Outcome.PROPOSE
    # Off: the accounts are not read at all.
    assert results(short)["reserve"] is Outcome.OK
