"""Project numbers in the company's own format, so a generated number looks like the ones the
owner already writes on orders and invoices.

A format is text with tokens: ``{yyyy}`` and ``{yy}`` (the year in the company's business time),
and ``{seq}`` or ``{seq:N}`` (the counter, zero-padded to N digits). "P{yy}-{seq:3}" gives
"P26-001". The counter is per company and never restarts, so a number stays unique whatever the
year token does.
"""

import re
from datetime import datetime
from typing import Final

DEFAULT_FORMAT: Final = "P{yy}-{seq:3}"
MAX_LENGTH: Final = 60

_TOKEN: Final = re.compile(r"\{([^{}]*)\}")
_SEQ: Final = re.compile(r"seq(?::([1-9]))?")


class InvalidFormat(ValueError):
    pass


def validate(number_format: str) -> str:
    tokens = _TOKEN.findall(number_format)
    if sum(1 for token in tokens if _SEQ.fullmatch(token)) != 1:
        raise InvalidFormat("A number format needs exactly one {seq} or {seq:N}")
    unknown = [t for t in tokens if t not in ("yyyy", "yy") and not _SEQ.fullmatch(t)]
    if unknown:
        raise InvalidFormat(f"Unknown token {{{unknown[0]}}}: use {{yyyy}}, {{yy}} or {{seq:N}}")
    if "{" in _TOKEN.sub("", number_format) or "}" in _TOKEN.sub("", number_format):
        raise InvalidFormat("A brace outside a token")
    if len(render(number_format, datetime(2000, 1, 1), 10**9)) > MAX_LENGTH:
        raise InvalidFormat(f"A number longer than {MAX_LENGTH} characters")
    return number_format


def render(number_format: str, now: datetime, seq: int) -> str:
    def token(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "yyyy":
            return f"{now.year:04d}"
        if name == "yy":
            return f"{now.year % 100:02d}"
        seq_match = _SEQ.fullmatch(name)
        if seq_match is None:  # validate() refused it before it was stored
            raise InvalidFormat(f"Unknown token {{{name}}}")
        width = int(seq_match.group(1) or 1)
        return f"{seq:0{width}d}"

    return _TOKEN.sub(token, number_format)


def normalise(number: str) -> str:
    """The form numbers are compared in: "p26-001", "P26 - 001" and "P26-001" are one number."""
    return re.sub(r"\s+", "", number).upper()
