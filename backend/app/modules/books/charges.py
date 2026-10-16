"""What a bill can charge for: kinds a sector pack registers (freight, waiting at a border), each
with what tells it apart, which the bill reader is shown. The core holds only the registry; a
line that fits none is "other"."""

from dataclasses import dataclass
from typing import Final

OTHER: Final = "other"


@dataclass(frozen=True, slots=True)
class Charge:
    key: str
    # What tells it apart, in a few words, for the reader.
    hint: str


_charges: dict[str, Charge] = {}


def register(charge: Charge) -> None:
    _charges[charge.key] = charge


def charges() -> tuple[Charge, ...]:
    return (*_charges.values(), Charge(OTHER, "anything else"))
