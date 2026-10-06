"""Facts a freight job's text holds, read by code: consignment note (CMR) numbers, plates named by
their label, and the order and booking references a load travels under (docs/architecture/09).

Plates have no one format across countries, so only a plate written after its label ("Truck:",
"Trailer plate:") is read here; a country pack reads its own plates wherever they are written.
A label that says what the vehicle is ("Trailer:", "Tractor:") is kept as the plate's role too
(``TRUCK_ROLE``, ``TRAILER_ROLE``): a country pack writes the same kinds for its own words.
"""

from collections.abc import Iterable
from typing import Final

from app.modules.facts.extractors import REGEX, after_label, is_quantity, is_reference, reference
from app.modules.facts.service import NewFact

# A plate's role, where its label says it: the truck that pulls, or the trailer it pulls.
PLATE: Final = "plate"
TRUCK_ROLE: Final = "truck_plate"
TRAILER_ROLE: Final = "trailer_plate"
_CMR: Final = after_label(r"\bCMR")
_PLATE: Final = after_label(
    r"\b(?:truck|trailer|tractor|lorry|vehicle|reg(?:istration)?)(?:\s+plates?)?|\bplates?",
    # Capitals, in at most three groups ("34 ABC 12", "CA 1234 AB", "B-123-ABC"), so the words
    # after a plate stay out of it.
    value=r"(?-i:[A-Z0-9]{1,4}(?:[ \-][A-Z0-9]{1,5}){0,2})(?![A-Za-z0-9])",
)
_REFERENCE: Final = r"[A-Z0-9][A-Z0-9/\-]{2,30}"
_ORDER: Final = after_label(r"\b(?:order|booking|PO|ref(?:erence)?)", value=_REFERENCE)
# "Load: 2400 KG" and "Shipment 12000 kg" are weights: these words name a reference only with a
# marker after them ("Load no", "Shipment ref", "Consignment #").
_LOAD: Final = after_label(
    r"\b(?:load|shipment|consignment)(?:\s*(?:no|nr|number|ref(?:erence)?|id)\b|(?=\s*#))",
    value=_REFERENCE,
)


def cmr_numbers(text: str) -> Iterable[NewFact]:
    for match in _CMR.finditer(text):
        number = match.group(1).rstrip(".-/")
        if is_reference(number):
            yield NewFact("cmr_number", number, reference(number), 0.9, REGEX)


def labelled_plates(text: str) -> Iterable[NewFact]:
    for match in _PLATE.finditer(text):
        plate = match.group(1).strip(" -")
        compact = reference(plate)
        # A plate has letters and digits; "Truck: 2" or "Trailer: TBC" are not plates.
        if 4 <= len(compact) <= 10 and any(c.isalpha() for c in compact) and is_reference(plate):
            yield NewFact(PLATE, plate, compact, 0.75, REGEX)
            label = match.group(0)[: match.start(1) - match.start(0)].lower()
            if "trailer" in label:
                yield NewFact(TRAILER_ROLE, plate, compact, 0.75, REGEX)
            elif any(word in label for word in ("truck", "tractor", "lorry")):
                yield NewFact(TRUCK_ROLE, plate, compact, 0.75, REGEX)


def order_numbers(text: str) -> Iterable[NewFact]:
    for pattern in (_ORDER, _LOAD):
        for match in pattern.finditer(text):
            number = match.group(1).rstrip(".-/")
            if (
                is_reference(number)
                and len(reference(number)) >= 4
                and not is_quantity(text, match.end(1))
            ):
                yield NewFact("order_number", number, reference(number), 0.8, REGEX)
