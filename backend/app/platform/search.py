"""Search text: one folded form for what is stored and what is typed, so a search finds text however
either was written.

Folding lowers case, drops accents and the Turkish dots, and turns anything but letters and digits
into single spaces: `İstanbul`, `ISTANBUL` and `istanbul` fold alike, and `Ödeme` to `odeme`, as
people type Turkish without its letters. A search matches when every term is found:
- a word, at the start of a word: `fatura` finds `Faturası`, and `tır` finds `TIR` but not
  `yatırım`;
- a term with a digit in it (a plate, an invoice, order or CMR number), anywhere, with spaces and
  punctuation ignored: `34abc12` finds `34 ABC 12` and `CMR-34ABC12.pdf`, and `0142` finds
  `FTR-2026-0142`;
- a phrase in double quotes, as those words in that order.

The stored text starts with a space, so every word, the first included, follows one; the fields are
kept apart by `|`, so no match runs from one field into the next. Both columns that hold it have a
trigram index (`pg_trgm`), which serves exactly these `LIKE '%…%'` matches.
"""

import re
import unicodedata
from dataclasses import dataclass

FIELD_BREAK = " | "
_NOT_WORD = re.compile(r"[\W_]+")


@dataclass(frozen=True, slots=True)
class Term:
    # Folded words, one space apart.
    words: str
    # Matched anywhere with spaces ignored, rather than at the start of a word.
    anywhere: bool

    @property
    def compact(self) -> str:
        return self.words.replace(" ", "")


def fold(text: str) -> str:
    # NFKD splits a letter from its accent (and `İ` from its dot), which then go; the dotless `ı`
    # has no dot to split off and is mapped by hand.
    split = unicodedata.normalize("NFKD", text)
    bare = "".join(char for char in split if not unicodedata.combining(char))
    return _NOT_WORD.sub(" ", bare.replace("ı", "i").casefold()).strip()


def search_text(*fields: str | None) -> str:
    """The fields of a record folded, kept apart, after a leading space."""
    return " " + FIELD_BREAK.join(folded for field in fields if field and (folded := fold(field)))


def terms(query: str) -> list[Term]:
    """The terms of a typed query; a phrase in double quotes is one term."""
    found: list[Term] = []
    for index, part in enumerate(query.split('"')):
        # Outside quotes (even places) each word is a term; inside them the phrase is one.
        pieces = part.split() if index % 2 == 0 else [part]
        for piece in pieces:
            if words := fold(piece):
                found.append(Term(words, anywhere=any(char.isdigit() for char in words)))
    return found
