from collections.abc import Iterable

from app.modules.facts import extractors
from app.modules.facts.extractors import ibans, invoice_numbers
from app.modules.facts.service import NewFact


def found(facts: Iterable[NewFact]) -> set[tuple[str, str]]:
    return {(fact.kind, fact.normalised) for fact in facts}


def test_an_iban_is_read_only_when_its_check_digits_hold() -> None:
    text = """Please pay to TR33 0006 1005 1978 6457 8413 26 TL, or DE89370400440532013000.
Not this one: TR33 0006 1005 1978 6457 8413 27."""

    facts = list(ibans(text))

    assert found(facts) == {
        ("iban", "TR330006100519786457841326"),
        ("iban", "DE89370400440532013000"),
    }
    # The word written after it is not part of it.
    assert "TR33 0006 1005 1978 6457 8413 26" in {fact.value for fact in facts}


def test_an_invoice_number_is_read_after_its_label_and_a_date_is_not_one() -> None:
    text = "Invoice No: 2026/118 attached. Invoice: 01.10.2026. Invoice attached. Invoices 12345"

    assert found(invoice_numbers(text)) == {("invoice_number", "2026118")}


def test_every_extractor_runs_and_a_value_is_kept_once_at_its_best_confidence() -> None:
    extractors.register("test.low", lambda text: [NewFact("x", "a", "A", 0.2, "t")])
    extractors.register("test.high", lambda text: [NewFact("x", "a", "A", 0.9, "t")])
    try:
        facts = [fact for fact in extractors.from_text("anything") if fact.kind == "x"]
    finally:
        extractors._extractors.pop("test.low")
        extractors._extractors.pop("test.high")

    assert [(fact.normalised, fact.confidence) for fact in facts] == [("A", 0.9)]
