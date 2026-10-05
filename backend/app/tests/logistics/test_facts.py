from collections.abc import Iterable

from app.modules.facts.service import NewFact
from app.packs.logistics.facts import cmr_numbers, labelled_plates, order_numbers


def found(facts: Iterable[NewFact]) -> set[tuple[str, str]]:
    return {(fact.kind, fact.normalised) for fact in facts}


def test_a_cmr_number_is_read_after_its_label() -> None:
    text = "CMR No: 0048213. The CMR is attached. CMR: 77-1234"

    assert found(cmr_numbers(text)) == {("cmr_number", "0048213"), ("cmr_number", "771234")}


def test_a_plate_is_read_after_its_label_and_stops_before_the_next_word() -> None:
    text = """Truck: CA 1234 AB arrives Monday.
Trailer plate: B-123-XYZ
Truck: TBC
Truck: 2"""

    assert found(labelled_plates(text)) == {
        ("plate", "CA1234AB"),
        ("plate", "B123XYZ"),
        # What the label says the vehicle is, kept as the plate's role.
        ("truck_plate", "CA1234AB"),
        ("trailer_plate", "B123XYZ"),
    }


def test_order_and_booking_references_need_a_digit_and_a_whole_label() -> None:
    text = "Booking no. BK-2026-118, PO 4500123. Order: please load. The port is closed."

    assert found(order_numbers(text)) == {
        ("order_number", "BK2026118"),
        ("order_number", "4500123"),
    }


def test_a_weight_after_a_load_or_shipment_is_not_a_reference() -> None:
    text = """Load: 2400 KG
Shipment 12000 kg on Monday.
Order: 3300 kgs
Load no: LD-44821, shipment ref 7781-A, consignment # 55120."""

    assert found(order_numbers(text)) == {
        ("order_number", "LD44821"),
        ("order_number", "7781A"),
        ("order_number", "55120"),
    }
