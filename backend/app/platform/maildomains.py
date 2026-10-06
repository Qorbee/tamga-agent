"""Mail domains: the one form they are compared in, and the public providers whose domain names no
one.

People write a domain as "Yuzbasi.com.tr", "@yuzbasi.com.tr", "www.yuzbasi.com.tr", a web address or a
whole mail address; ``domain_of`` reduces each to "yuzbasi.com.tr". A name in another script keeps
its IDNA (punycode) form, as mail carries it; ``folded`` gives the plain-letter spelling a keyboard
may have meant ("yuzbası.com.tr" for "yuzbasi.com.tr").

Anyone can have an address at a public provider, so its domain says nothing about who writes from
it: it is never the company's, and never a firm's. Packs add their country's providers.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

_SHARED: Final[set[str]] = {
    "aol.com",
    "gmail.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "googlemail.com",
    "hotmail.com",
    "hotmail.co.uk",
    "icloud.com",
    "live.com",
    "mac.com",
    "mail.com",
    "mail.ru",
    "me.com",
    "msn.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "web.de",
    "windowslive.com",
    "yahoo.com",
    "yandex.com",
    "yandex.ru",
    "ymail.com",
    "zoho.com",
}
_LABEL: Final = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")


def register_shared(domains: Iterable[str]) -> None:
    """A pack's public providers ("hotmail.com.tr")."""
    _SHARED.update(domain.lower() for domain in domains)


def is_shared(domain: str) -> bool:
    return domain in _SHARED


def domain_of(written: str) -> str | None:
    """The domain in what was written, lower-cased and in IDNA form; None when it holds none."""
    text = written.strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)  # a web address's scheme
    text = text.rpartition("@")[2]  # an address's local part
    text = re.split(r"[/?#:\s]", text, maxsplit=1)[0].strip(".")
    text = text.removeprefix("www.")
    try:
        ascii_form = text.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = ascii_form.split(".")
    if len(ascii_form) > 253 or len(labels) < 2 or not all(map(_LABEL.fullmatch, labels)):
        return None
    # The last label is the top-level domain: letters, or an IDNA one.
    if not (labels[-1].isalpha() or labels[-1].startswith("xn--")) or len(labels[-1]) < 2:
        return None
    return ascii_form


def folded(domain: str) -> str | None:
    """The plain-letter spelling of an IDNA domain ("xn--yuzbas-…" → "yuzbasi.com.tr"), or None when
    it has nothing to fold. Turkish dotless and dotted i both become i, as names do."""
    try:
        written = domain.encode("ascii").decode("idna")
    except UnicodeError:
        return None
    plain = unicodedata.normalize("NFKD", written.replace("ı", "i").replace("İ", "i"))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    if plain == domain or not plain.isascii():
        return None
    return domain_of(plain)


def spellings(domain: str) -> list[str]:
    """The ways a domain is stored: its IDNA form, and for a name in another script its Unicode
    form too, as an address on a card or in a mail header may keep it."""
    try:
        unicode_form = domain.encode("ascii").decode("idna")
    except UnicodeError:
        return [domain]
    return [domain] if unicode_form == domain else [domain, unicode_form]


def within(domain: str, parent: str) -> bool:
    """Whether ``domain`` is ``parent`` or one of its subdomains ("ops.yuzbasi.com.tr")."""
    return domain == parent or domain.endswith(f".{parent}")


def address_domain(address: str) -> str | None:
    """The domain of a mail address, or None when it is not one."""
    local, at, _ = address.strip().rpartition("@")
    return domain_of(address) if at and local else None


# Second-level labels a country registry sells under ("com.tr", "co.uk"): the name is the label
# before them.
_SECOND_LEVEL: Final = frozenset(
    {"ac", "av", "bel", "biz", "co", "com", "edu", "gen", "gov", "info", "ltd", "net", "org", "web"}
)


def name_of(domain: str) -> str:
    """The label a domain was registered as, as it is read: "atlascompany" in
    "ops.atlascompany.com", "yuzbasi" in "yuzbasi.com.tr", "yüzbaşı" in its IDNA form."""
    labels = spellings(domain)[-1].split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return labels[-3]
    return labels[-2]
