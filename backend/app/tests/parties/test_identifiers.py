import pytest

from app.modules.parties.identifiers import normalise, normalise_name
from app.modules.parties.models import IdentifierKind


@pytest.mark.parametrize(
    "written",
    ["0532 123 45 67", "+90 532 123 45 67", "0090 532 1234567", "905321234567", "532-123-4567"],
)
def test_a_turkish_mobile_is_one_phone_however_it_is_written(written: str) -> None:
    assert normalise(IdentifierKind.PHONE, written, country="tr") == "+905321234567"


def test_a_foreign_phone_keeps_its_own_country() -> None:
    assert normalise(IdentifierKind.PHONE, "+49 151 1234 5678", country="tr") == "+4915112345678"


@pytest.mark.parametrize(
    "written", ["1234", "dahili 105", "+90 532 123 45 67 89 01 23", "0532 123", "+90 532 123"]
)
def test_what_cannot_be_a_phone_is_dropped(written: str) -> None:
    assert normalise(IdentifierKind.PHONE, written, country="tr") is None


def test_a_national_phone_needs_a_known_country() -> None:
    assert normalise(IdentifierKind.PHONE, "0532 123 45 67", country="xx") is None


def test_an_iban_is_kept_only_when_its_check_digits_hold() -> None:
    valid = "TR33 0006 1005 1978 6457 8413 26"
    assert normalise(IdentifierKind.IBAN, valid, country="tr") == "TR330006100519786457841326"
    assert normalise(IdentifierKind.IBAN, valid.replace("26", "27"), country="tr") is None


def test_an_email_is_compared_in_lower_case() -> None:
    assert normalise(IdentifierKind.EMAIL, " <Muhasebe@Kaya.example> ", country="tr") == (
        "muhasebe@kaya.example"
    )
    assert normalise(IdentifierKind.EMAIL, "not an address", country="tr") is None


def test_plates_and_tax_ids_lose_spacing_and_case() -> None:
    assert normalise(IdentifierKind.PLATE, "34 abc 12", country="tr") == "34ABC12"
    assert normalise(IdentifierKind.TAX_ID, "123 456 7890", country="tr") == "1234567890"


def test_turkish_names_match_whichever_i_and_accents_were_typed() -> None:
    assert normalise_name("ÖZDEMİR Nakliyat Ltd. Şti.") == "ozdemir nakliyat ltd sti"
    assert normalise_name("Ozdemir  nakliyat LTD STI") == "ozdemir nakliyat ltd sti"
    assert normalise_name("Işık Lojistik") == normalise_name("ISIK LOJİSTİK")
