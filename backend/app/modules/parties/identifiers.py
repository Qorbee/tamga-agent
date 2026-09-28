"""The one form each kind of identifier is compared in.

The same phone arrives as "0532 123 45 67" in a signature, "+90 532 1234567" on WhatsApp and
"905321234567" on an accounting card. A value that cannot be what it claims (a four-digit phone, an
IBAN whose check digits fail) normalises to None and is not stored: a wrong identifier links the
wrong party, which is worse than a missing one.
"""

import re
import unicodedata
from typing import Final

from app.modules.parties.models import IdentifierKind

# For phones written in national form ("0532 …"), by the company's country. Moves into the country
# packs when a second country needs one.
CALLING_CODES: Final = {"tr": "90"}
# How many digits follow a calling code whose numbers all have one length: Turkey's are ten. A
# number under such a code with any other length is a fragment ("0532 123"), not a phone.
_NATIONAL_DIGITS: Final = {"90": 10}
_IBAN: Final = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")


def normalise(kind: IdentifierKind, value: str, *, country: str) -> str | None:
    match kind:
        case IdentifierKind.PHONE:
            return _phone(value, CALLING_CODES.get(country))
        case IdentifierKind.EMAIL:
            return _email(value)
        case IdentifierKind.IBAN:
            return _iban(value)
        case IdentifierKind.TAX_ID:
            return _alnum(value, min_length=5)
        case IdentifierKind.PLATE:
            return _alnum(value, min_length=3)
        case IdentifierKind.NAME:
            return normalise_name(value) or None


def normalise_name(value: str) -> str:
    """Case, accents and punctuation removed: "ÖZDEMİR Nakliyat Ltd. Şti." → "ozdemir nakliyat ltd
    sti". Turkish dotted and dotless i both become i, as people type them either way."""
    text = unicodedata.normalize("NFKD", value.replace("ı", "i").replace("İ", "i").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", text))


def _phone(value: str, calling_code: str | None) -> str | None:
    written = value.strip()
    digits = re.sub(r"\D", "", written)
    if written.startswith("+"):
        full = digits
    elif digits.startswith("00"):
        full = digits[2:]
    elif calling_code is None:
        return None  # a national number, and no country to read it in
    elif digits.startswith("0"):
        full = calling_code + digits[1:]
    elif digits.startswith(calling_code) and len(digits) > _NATIONAL_DIGITS.get(calling_code, 0):
        full = digits
    else:
        full = calling_code + digits
    for code, length in _NATIONAL_DIGITS.items():
        if full.startswith(code):
            return f"+{full}" if len(full) == len(code) + length else None
    # E.164: at most 15 digits; fewer than 8 is an extension or a fragment, not a phone.
    return f"+{full}" if 8 <= len(full) <= 15 else None


def _email(value: str) -> str | None:
    email = value.strip().removeprefix("mailto:").strip("<>").lower()
    local, at, domain = email.rpartition("@")
    if not at or not local or "." not in domain or any(char.isspace() for char in email):
        return None
    return email


def _iban(value: str) -> str | None:
    iban = _alnum(value, min_length=15)
    if iban is None or not _IBAN.fullmatch(iban):
        return None
    # ISO 13616: move the country and check digits to the end, read letters as 10–35, mod 97 is 1.
    rearranged = iban[4:] + iban[:4]
    return iban if int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1 else None


def _alnum(value: str, *, min_length: int) -> str | None:
    text = re.sub(r"[^0-9A-Z]", "", value.upper())
    return text if len(text) >= min_length else None
