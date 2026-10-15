"""The company's own payment rules (ADR-0033): conditions from a fixed catalogue, checked like a
form, run by code after the hard rules, and never able to let through what those hold."""

import dataclasses
import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.policy import custom
from app.modules.policy import service as policies
from app.modules.policy.custom import Condition, CustomRule, Facts
from app.modules.policy.models import PaymentMethod
from app.modules.policy.schemas import Outcome, PolicyIn
from app.tests.decisions.test_rules import CLEAR

NEEDS_CASH = uuid.uuid4()
FIRM = uuid.uuid4()
FACTS = Facts(
    party_id=FIRM,
    groups=frozenset({NEEDS_CASH}),
    known_groups=frozenset({NEEDS_CASH}),
    party_kind="person",
    issued_on=date(2026, 9, 1),
    due_on=date(2026, 10, 10),
    today=date(2026, 10, 15),
    in_policy_currency=Decimal("2204.00"),
    currency="EUR",
    method=PaymentMethod.BANK_TRANSFER,
    client_paid=True,
)


def rule(then: custom.Effect, *when: Condition) -> CustomRule:
    return CustomRule(key="rule:a1", when=list(when), then=then)


def test_a_condition_takes_only_what_its_attribute_takes() -> None:
    Condition(attribute="days_owed", operator="above", value=30)
    for wrong in (
        {"attribute": "days_owed", "operator": "in", "value": 30},
        {"attribute": "days_owed", "operator": "above", "value": "thirty"},
        {"attribute": "group", "operator": "in", "value": ["not an id"]},
        {"attribute": "currency", "operator": "in", "value": ["euro"]},
        {"attribute": "firm_kind", "operator": "is", "value": "carrier"},
        {"attribute": "client_paid", "operator": "is", "value": "yes"},
        {"attribute": "amount", "operator": "below", "value": -1},
    ):
        with pytest.raises(ValidationError):
            Condition.model_validate(wrong)
    with pytest.raises(ValidationError):
        CustomRule(key="rule:a1", when=[], then="hold")


def test_each_attribute_reads_the_bills_facts() -> None:
    holding = [
        Condition(attribute="group", operator="in", value=[str(NEEDS_CASH)]),
        Condition(attribute="firm", operator="in", value=[str(FIRM)]),
        Condition(attribute="firm_kind", operator="is", value="person"),
        Condition(attribute="days_owed", operator="above", value=30),
        Condition(attribute="days_to_due", operator="below", value=0),
        Condition(attribute="amount", operator="above", value=Decimal(2000)),
        Condition(attribute="currency", operator="in", value=["EUR"]),
        Condition(attribute="method", operator="is", value="bank_transfer"),
        Condition(attribute="client_paid", operator="is", value=True),
    ]
    assert all(custom.holds(each, FACTS) for each in holding)
    assert not custom.holds(
        Condition(attribute="group", operator="not_in", value=[str(NEEDS_CASH)]), FACTS
    )
    # A fact not known never holds: a bill with no rate is not "above" anything.
    unknown = dataclasses.replace(FACTS, in_policy_currency=None, due_on=None, client_paid=None)
    assert not custom.holds(holding[5], unknown)
    assert not custom.holds(holding[4], unknown)
    assert not custom.holds(holding[8], unknown)


def test_a_rule_asks_holds_or_orders_and_the_worst_still_decides() -> None:
    cash_first = rule("first", Condition(attribute="group", operator="in", value=[str(NEEDS_CASH)]))
    old_ask = rule("ask", Condition(attribute="days_owed", operator="above", value=30))
    own = policies.DEFAULT.model_copy(
        update={"approval_limit": Decimal("5000.00"), "custom_rules": [cash_first]}
    )
    outcome, results = policies.evaluate(CLEAR, own, FACTS)
    assert outcome is Outcome.OK
    assert custom.order_of([result.rule for result in results]) == -1

    asked = own.model_copy(update={"custom_rules": [old_ask.model_copy(update={"key": "rule:b2"})]})
    outcome, results = policies.evaluate(CLEAR, asked, FACTS)
    assert outcome is Outcome.PROPOSE
    assert [r.rule for r in results if r.rule.startswith("custom:")] == ["custom:ask:rule:b2"]
    # No rule of the company's own lets through what a hard rule holds.
    held = dataclasses.replace(CLEAR, trucks=(False,))
    outcome, _ = policies.evaluate(held, own, FACTS)
    assert outcome is Outcome.HOLD
    # Without facts, or not applying, a rule of its own adds nothing.
    assert policies.evaluate(CLEAR, asked) == policies.evaluate(
        CLEAR, asked.model_copy(update={"custom_rules": []})
    )


def test_first_wins_over_last_and_nothing_else_moves_a_bill() -> None:
    assert custom.order_of(["custom:last:rule:a", "custom:first:rule:b"]) == -1
    assert custom.order_of(["custom:last:rule:a", "client_paid"]) == 1
    assert custom.order_of(["client_paid", "custom:hold:rule:c"]) == 0


def test_a_policy_holds_each_rule_of_its_own_once() -> None:
    one = rule("hold", Condition(attribute="days_owed", operator="above", value=60))
    with pytest.raises(ValidationError):
        PolicyIn(
            currency="EUR", approval_limit=Decimal(1), pay_when_paid=True, custom_rules=[one, one]
        )


def test_a_deleted_group_holds_for_nobody_either_way() -> None:
    gone = str(uuid.uuid4())
    for operator in ("in", "not_in"):
        condition = Condition(attribute="group", operator=operator, value=[gone])
        assert not custom.holds(condition, FACTS)


def test_an_amount_with_cents_survives_being_stored() -> None:
    cents = rule("ask", Condition(attribute="amount", operator="above", value=Decimal("100.50")))
    stored = PolicyIn(
        currency="EUR", approval_limit=Decimal(1), pay_when_paid=True, custom_rules=[cents]
    ).model_dump(mode="json")
    again = PolicyIn.model_validate(stored)
    assert again.custom_rules[0].when[0].value == Decimal("100.50")
