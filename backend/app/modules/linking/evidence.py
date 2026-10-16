"""How well a proposed project is backed, weighed by code (docs/architecture/09).

A model's own confidence is not shown: nothing checks it, and stated confidence runs high on
wrong answers. What the owner reads is weighed from what the records show (what the conversation
tells of the job, the numbers it carries, how many mails it holds, whether the company is only
copied) and from how the owner answered Tamga's past proposals weighed the same way.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.modules.linking.schemas import Band, Gap

# Below this many answered proposals of a band, how many were kept says nothing yet.
MEASURED_FROM: Final = 10


@dataclass(frozen=True, slots=True)
class Signals:
    """What a conversation shows of the job it was proposed for."""

    # What the linker read of the job, each value as text.
    reading: Mapping[str, Any] | None
    mails: int
    # Document numbers its mails and files carry: an order, a CMR, an e-invoice id.
    numbers: int
    only_copied: bool


def gaps(reading: Mapping[str, Any] | None) -> list[Gap]:
    """What the conversation does not tell of the job: who gave it, where it goes, when it
    starts."""
    read = reading or {}
    missing: list[Gap] = []
    if not read.get("client"):
        missing.append("client")
    if not read.get("origin") and not read.get("destination"):
        missing.append("route")
    if not read.get("starts_on"):
        missing.append("first_day")
    return missing


def band(signals: Signals) -> Band:
    """Thin: the company is only copied, or neither the client nor the route is known. Strong:
    both are, and a document number or a second mail backs them. Partial: anything between."""
    missing = gaps(signals.reading)
    if signals.only_copied or ("client" in missing and "route" in missing):
        return "thin"
    known = "client" not in missing and "route" not in missing
    return "strong" if known and (signals.numbers > 0 or signals.mails >= 2) else "partial"
