"""The hard rules, as pure functions over a picture: each says what fails and the worst decides."""

import dataclasses
from decimal import Decimal

from app.modules.policy import service as policies
from app.modules.policy.schemas import Outcome, Picture, Vault

POLICY = policies.DEFAULT

CLEAR = Picture(
    amount=Decimal("1900.00"),
    left=Decimal("1900.00"),
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
    assert failed(dataclasses.replace(CLEAR, party=False))["vault_account"] == Outcome.HOLD
    # A wallet listed a moment ago waits out its timelock.
    assert failed(dataclasses.replace(CLEAR, payee_active_from=300))["timelock"] == Outcome.HOLD


def test_an_amount_is_never_guessed() -> None:
    no_rate = dataclasses.replace(CLEAR, currency="XYZ", usdc_units=None, in_policy_currency=None)
    assert failed(no_rate)["currency"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, left=None))["amount"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, left=Decimal("0.00")))["amount"] == Outcome.HOLD
    more = dataclasses.replace(CLEAR, amount=Decimal("2000.00"))
    assert failed(more)["amount"] == Outcome.HOLD
    assert failed(dataclasses.replace(CLEAR, cancelled=True))["amount"] == Outcome.HOLD


def test_a_bill_is_mirrored_at_its_currencys_rate_rounded_down() -> None:
    mirrored = policies.mirror(Decimal("1900.00"), "EUR")
    assert mirrored is not None and mirrored.units == 220_400
    assert policies.mirror(Decimal("1900.00"), "XYZ") is None
    # Compared through the ratio of the two rates, never figure against figure.
    assert policies.convert(Decimal("1900.00"), "EUR", "USD") == Decimal("2204.00")
