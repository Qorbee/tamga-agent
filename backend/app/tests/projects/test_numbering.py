from datetime import datetime

import pytest

from app.modules.projects import numbering


def test_a_format_renders_the_year_and_a_padded_counter() -> None:
    now = datetime(2026, 9, 29)
    assert numbering.render("P{yy}-{seq:3}", now, 7) == "P26-007"
    assert numbering.render("{yyyy}/{seq}", now, 1234) == "2026/1234"


@pytest.mark.parametrize(
    "number_format", ["P{yy}", "{seq}{seq}", "P{month}-{seq}", "P{yy-{seq}", "x" * 60 + "{seq}"]
)
def test_a_format_without_one_counter_or_with_unknown_tokens_is_refused(
    number_format: str,
) -> None:
    with pytest.raises(numbering.InvalidFormat):
        numbering.validate(number_format)


def test_numbers_compare_without_case_or_spaces() -> None:
    assert numbering.normalise(" p26 - 001") == numbering.normalise("P26-001")
