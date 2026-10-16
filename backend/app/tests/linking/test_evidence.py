from app.modules.linking.evidence import Signals, band, gaps


def signals(
    reading: dict[str, str] | None, *, mails: int = 1, numbers: int = 0, only_copied: bool = False
) -> Signals:
    return Signals(reading=reading, mails=mails, numbers=numbers, only_copied=only_copied)


JOB = {"client": "Stone", "origin": "Istanbul", "destination": "Munich", "starts_on": "2026-08-03"}


def test_a_job_whose_client_and_route_a_number_backs_is_strong() -> None:
    assert band(signals(JOB, numbers=1)) == "strong"
    # A second mail backs it as well as a number does.
    assert band(signals(JOB, mails=2)) == "strong"
    assert gaps(JOB) == []


def test_one_mail_with_no_number_is_partial_however_much_it_tells() -> None:
    assert band(signals(JOB)) == "partial"


def test_a_conversation_the_company_is_only_copied_on_is_thin() -> None:
    assert band(signals(JOB, mails=5, numbers=2, only_copied=True)) == "thin"


def test_neither_client_nor_route_is_thin_and_says_what_is_missing() -> None:
    reading = {"cargo": "3 trucks", "trucks": "3"}
    assert band(signals(reading, mails=4, numbers=1)) == "thin"
    assert gaps(reading) == ["client", "route", "first_day"]
    assert band(signals(None)) == "thin"


def test_a_route_without_a_client_is_partial() -> None:
    reading = {"destination": "Iraq"}
    assert band(signals(reading, mails=3, numbers=2)) == "partial"
    assert gaps(reading) == ["client", "first_day"]
