"""The company's own payment rules (ADR-0033): data, not code or a prompt. A rule says when (one to
five conditions, all of which hold) and then (an effect). Each condition reads one attribute from a
fixed catalogue that code works out for every bill, with the operators that attribute takes, so a
rule the chat drafts from someone's words is checked like a form, and the same bills always get
the same answer.

An effect only tightens or orders: it asks the owner, holds, or puts a bill first or last in the
run. No rule of a company's own can let through what a hard rule holds.

A condition that needs a fact code does not know for a bill (no reference rate, no due date) does
not hold, so the rule does not apply to that bill.
"""

import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.modules.policy.models import PaymentMethod

Attribute = Literal[
    "group",
    "firm",
    "firm_kind",
    "days_owed",
    "days_to_due",
    "amount",
    "currency",
    "method",
    "client_paid",
]
Operator = Literal["is", "in", "not_in", "above", "below"]
Effect = Literal["first", "last", "ask", "hold"]

# What each attribute takes: its operators, and what its value is.
_CATALOGUE: Final[dict[str, tuple[frozenset[str], str]]] = {
    # Groups of firms the company named (``party_groups``), by id.
    "group": (frozenset({"in", "not_in"}), "ids"),
    "firm": (frozenset({"in", "not_in"}), "ids"),
    "firm_kind": (frozenset({"is"}), "kind"),
    # Days since the bill was issued, and to its due date (below zero: overdue).
    "days_owed": (frozenset({"above", "below"}), "days"),
    "days_to_due": (frozenset({"above", "below"}), "days"),
    # The amount in the policy's currency, at the official reference rate.
    "amount": (frozenset({"above", "below"}), "money"),
    "currency": (frozenset({"in", "not_in"}), "currencies"),
    "method": (frozenset({"is"}), "method"),
    # Whether the client of the bill's job has paid it, as pay-when-paid reads it.
    "client_paid": (frozenset({"is"}), "yes_no"),
}
# A rule's result is recorded under this id: the effect in it, so a run can order by it.
PREFIX: Final = "custom"


class Condition(BaseModel):
    attribute: Attribute
    operator: Operator
    value: bool | int | Decimal | str | list[str]

    @model_validator(mode="before")
    @classmethod
    def _as_its_attribute_takes(cls, data: object) -> object:
        """A value stored as JSON comes back as the attribute takes it: an amount written as
        "100.50" is a number again, a count of days an integer."""
        if not isinstance(data, dict):
            return data
        shape = _CATALOGUE.get(str(data.get("attribute")), (frozenset(), ""))[1]
        value = data.get("value")
        if (
            shape == "money"
            and isinstance(value, str | float | int)
            and not isinstance(value, bool)
        ):
            # A value that is no number stays as it is and fails the check below.
            with contextlib.suppress(ArithmeticError):
                data = {**data, "value": Decimal(str(value))}
        elif shape == "days" and isinstance(value, str) and value.lstrip("-").isdigit():
            data = {**data, "value": int(value)}
        return data

    @model_validator(mode="after")
    def _fits_the_catalogue(self) -> Self:
        operators, shape = _CATALOGUE[self.attribute]
        if self.operator not in operators:
            raise ValueError(f"{self.attribute} takes {', '.join(sorted(operators))}")
        value = self.value
        if shape in ("ids", "currencies"):
            if not isinstance(value, list) or not 1 <= len(value) <= 50:
                raise ValueError(f"{self.attribute} takes a list of one to 50")
            if shape == "ids":
                for each in value:
                    uuid.UUID(each)
            elif any(len(each) != 3 or not each.isalpha() or not each.isupper() for each in value):
                raise ValueError("currencies are ISO codes such as EUR")
        elif shape == "kind" and value not in ("person", "organisation"):
            raise ValueError("firm_kind is person or organisation")
        elif shape == "method" and value not in {method.value for method in PaymentMethod}:
            raise ValueError("method is one of the ways to pay")
        elif shape == "yes_no" and not isinstance(value, bool):
            raise ValueError("client_paid is true or false")
        elif shape == "days" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{self.attribute} is a whole number of days")
        elif shape == "money":
            if isinstance(value, bool) or not isinstance(value, int | Decimal):
                raise ValueError("amount is a number in the policy's currency")
            if Decimal(value) < 0:
                raise ValueError("amount is zero or more")
        return self


class CustomRule(BaseModel):
    """One of the company's own payment rules."""

    key: str = Field(pattern=r"^rule:[a-z0-9]{1,16}$")
    when: list[Condition] = Field(min_length=1, max_length=5)
    then: Effect
    # The words it was asked for in, where a chat drafted it: shown, never run.
    said: str = Field(default="", max_length=300)


@dataclass(frozen=True, slots=True)
class Facts:
    """What the catalogue reads of one bill, as code found it."""

    party_id: uuid.UUID | None
    groups: frozenset[uuid.UUID] = field(default_factory=frozenset)
    # Every group the company has: a rule naming one deleted since cannot be read.
    known_groups: frozenset[uuid.UUID] = field(default_factory=frozenset)
    party_kind: str | None = None
    issued_on: date | None = None
    due_on: date | None = None
    today: date | None = None
    in_policy_currency: Decimal | None = None
    currency: str | None = None
    method: PaymentMethod | None = None
    # None where the bill's job has no client invoice to tell.
    client_paid: bool | None = None


def holds(condition: Condition, facts: Facts) -> bool:
    """Whether a condition holds for a bill; False where a fact it needs is not known."""
    value, operator = condition.value, condition.operator
    match condition.attribute:
        case "group":
            named = {uuid.UUID(each) for each in value} if isinstance(value, list) else set()
            if not named <= facts.known_groups:
                # A group deleted since: who was in it is not known, so neither "in" nor
                # "not in" it holds.
                return False
            inside = bool(facts.groups & named)
            return inside if operator == "in" else not inside
        case "firm":
            named = {uuid.UUID(each) for each in value} if isinstance(value, list) else set()
            if facts.party_id is None:
                return False
            return (facts.party_id in named) == (operator == "in")
        case "firm_kind":
            return facts.party_kind is not None and facts.party_kind == value
        case "days_owed":
            if facts.issued_on is None or facts.today is None:
                return False
            return _compare((facts.today - facts.issued_on).days, operator, value)
        case "days_to_due":
            if facts.due_on is None or facts.today is None:
                return False
            return _compare((facts.due_on - facts.today).days, operator, value)
        case "amount":
            if facts.in_policy_currency is None:
                return False
            return _compare(facts.in_policy_currency, operator, value)
        case "currency":
            if facts.currency is None or not isinstance(value, list):
                return False
            return (facts.currency in value) == (operator == "in")
        case "method":
            return facts.method is not None and facts.method.value == value
        case "client_paid":
            return facts.client_paid is not None and facts.client_paid == value
    return False


def _compare(seen: int | Decimal, operator: str, value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return False
    return seen > value if operator == "above" else seen < value


def applies(rule: CustomRule, facts: Facts) -> bool:
    return all(holds(condition, facts) for condition in rule.when)


def result_id(rule: CustomRule) -> str:
    """``custom:<effect>:<key>``: a run orders by the effect without reading the policy."""
    return f"{PREFIX}:{rule.then}:{rule.key}"


def order_of(rule_ids: list[str]) -> int:
    """Where a bill goes in the run by the rules that applied to it: -1 first, 1 last, else 0.
    First wins over last, as the more urgent word."""
    effects = {rule_id.split(":")[1] for rule_id in rule_ids if rule_id.startswith(f"{PREFIX}:")}
    if "first" in effects:
        return -1
    return 1 if "last" in effects else 0
