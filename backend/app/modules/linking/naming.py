"""What code accepts of a name the project titler wrote (docs/architecture/09, Naming): the
model writes, code checks every word against what it was shown (ADR-0023). A name with a word
it was not given, a mail's own words ("FW:", "invoice"), an address, or too few or too many
words is refused, and code's own name stands."""

import re
import unicodedata
from typing import Final

# Joins a name's parts, as code's own names do.
JOIN: Final = " · "
# How many words a name has: a client alone tells no job apart; more than this is a sentence.
FEWEST, MOST = 2, 8
# What a model may answer when it is given too little to tell the job apart.
NONE: Final = "NONE"
# The longest name kept.
LONGEST: Final = 120

_WRAPPED: Final = " \"'“”‘’«».`"
_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)
_ADDRESS: Final = re.compile(r"\S+@\S+|(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")
# What a mail's subject says of itself, never of a job; a pack adds its language's.
_noise: set[str] = {"fw", "fwd", "re", "aw", "invoice", "quote", "quotation", "offer", "order"}


def register_noise(words: set[str]) -> None:
    _noise.update(fold(word) for word in words)


def fold(text: str) -> str:
    """Lower case without accents, so "İZMİR" and "izmir" are one word."""
    text = text.replace("İ", "i").replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def words_of(text: str) -> set[str]:
    return set(_WORD.findall(fold(text)))


def checked(said: str, shown: str) -> str | None:
    """The titler's answer as a name, or None when code refuses it: every word of it must be in
    what the titler was shown (``shown``); none may be only a mail's own word or an address;
    and it has two to eight words."""
    name = " ".join(said.split()).strip(_WRAPPED)[:LONGEST]
    if not name or name.upper() == NONE:
        return None
    # A word as a reader counts it: "05.10.2026" is one; the joins and the arrow are none.
    if not FEWEST <= sum(bool(_WORD.search(token)) for token in name.split()) <= MOST:
        return None
    words = _WORD.findall(fold(name))
    if any(_ADDRESS.search(part) for part in name.split()):
        return None
    if not set(words) <= words_of(shown):
        return None
    # A part made only of a mail's own words names nothing of the job.
    for part in name.split(JOIN):
        own = words_of(part)
        if own and own <= _noise:
            return None
    return name
