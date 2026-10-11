"""The reserve: what the company's bank and cash accounts must keep after a payment, and how
Tamga leans when it weighs one. The owner picks one of the presets, or a rule of their own made
by duplicating one and changing it; a preset never changes under a policy version, since the
version keeps a copy of the rule it put in force."""

from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, Field

# A preset's key; the owner's own rules are keyed "own:" and a short id of their own.
OWN: Final = "own:"


class Stance(StrEnum):
    """How the controller leans when it weighs a bill the rules allow."""

    # Pays only what is due, and holds what can wait.
    CAREFUL = "careful"
    BALANCED = "balanced"
    # Pays when it can, to keep the company's subcontractors close.
    LEAN = "lean"


class ReserveRule(BaseModel):
    key: str = Field(pattern=r"^(off|light|steady|careful|own:[a-z0-9]{1,12})$")
    # The owner's name for a rule of their own; a preset's is shown in the app's language.
    name: str = Field(min_length=1, max_length=60)
    # The preset a rule of the owner's was duplicated from.
    based_on: str | None = Field(default=None, pattern=r"^(off|light|steady|careful)$")
    # Off: no payment is weighed against the accounts.
    active: bool
    # The bills due within this many weeks stay covered after a payment, the overdue ones too.
    cover_weeks: int = Field(ge=0, le=12)
    # And this much more, in the policy's currency.
    floor: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    # Under the reserve, a payment is held, or left to the owner with the reason.
    on_breach: Literal["hold", "ask"]
    stance: Stance


PRESETS: Final[tuple[ReserveRule, ...]] = (
    ReserveRule(
        key="off",
        name="Off",
        active=False,
        cover_weeks=0,
        floor=Decimal("0.00"),
        on_breach="ask",
        stance=Stance.BALANCED,
    ),
    ReserveRule(
        key="light",
        name="Light",
        active=True,
        cover_weeks=0,
        floor=Decimal("0.00"),
        on_breach="ask",
        stance=Stance.LEAN,
    ),
    ReserveRule(
        key="steady",
        name="Steady",
        active=True,
        cover_weeks=2,
        floor=Decimal("0.00"),
        on_breach="ask",
        stance=Stance.BALANCED,
    ),
    ReserveRule(
        key="careful",
        name="Careful",
        active=True,
        cover_weeks=4,
        floor=Decimal("0.00"),
        on_breach="hold",
        stance=Stance.CAREFUL,
    ),
)
_BY_KEY: Final = {preset.key: preset for preset in PRESETS}
OFF: Final = _BY_KEY["off"]
# How many rules of their own an owner keeps.
MAX_OWN: Final = 10


def resolve(key: str, own: list[ReserveRule]) -> ReserveRule | None:
    """The preset or the owner's rule with this key, or None."""
    if key in _BY_KEY:
        return _BY_KEY[key]
    return next((rule for rule in own if rule.key == key), None)


def check_own(own: list[ReserveRule]) -> str | None:
    """What is wrong with the owner's own rules, or None."""
    if len(own) > MAX_OWN:
        return f"At most {MAX_OWN} reserve rules of your own"
    if any(not rule.key.startswith(OWN) or rule.based_on is None for rule in own):
        return "A reserve rule of your own is keyed own: and duplicated from a preset"
    if len({rule.key for rule in own}) != len(own):
        return "Each reserve rule once"
    return None


STANCES: Final = {
    Stance.CAREFUL: "The owner is careful with cash: propose paying only what is due or overdue, "
    "and hold what can wait.",
    Stance.BALANCED: "The owner pays what is due and keeps the accounts healthy.",
    Stance.LEAN: "The owner pays when the rules allow, to keep subcontractors close: propose "
    "paying early when nothing argues against it.",
}
