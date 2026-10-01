"""A reply's own text: what its writer typed, without the history quoted below it.

Most work runs as long reply chains, and each reply carries the whole chain again. Facts are read
from the mail that said them, and a model reads a reply without paying for the chain, so the quoted
history is cut where a mail client starts it (docs/architecture/06, Mail):
- a header block, as Outlook writes it: a "From" line followed closely by a subject line or a
  sent line with a clock time, in the languages the company's partners write in;
- a "... wrote:" line, as Gmail and Apple Mail write it;
- an "Original Message" rule;
- lines quoted with ">".

A forward keeps everything: the mail it carries may be the only copy Tamga ever sees.
"""

import re
from typing import Final

# The labels a mail client writes above quoted history, in the languages the company's partners
# write in. A "From" line starts a block.
_FROM: Final = {"from", "kimden", "de", "de la", "von", "od", "от", "da", "van", "från", "fra"}
_SUBJECT: Final = {
    "subject", "konu", "objet", "asunto", "assunto", "oggetto", "subiect", "betreff", "onderwerp",
    "temat", "ämne", "emne", "тема",
}  # fmt: skip
_SENT: Final = {
    "sent", "gönderildi", "gönderilme tarihi", "gönderme tarihi", "envoyé", "enviado", "inviato",
    "trimis", "gesendet", "verzonden", "wysłano", "skickat", "sendt", "отправлено", "изпратено",
}  # fmt: skip
# A plain "Date" label heads an order's route as often as a mail's headers ("From: Istanbul",
# "To: Munich", "Date: 05.10.2026 09:00"), so it counts only below a "From" that names an address.
_DATE: Final = {"date", "tarih", "fecha", "data", "datum", "дата"}
# A header line: optional bold stars (Outlook's text part), the label, a colon, the value.
_LABEL: Final = re.compile(
    r"^\s*\*{0,2}\s*([^\W\d_][^\W\d_ ]*(?: [^\W\d_]+)?)\s*\*{0,2}\s*:\s*(.*)$", re.I
)
_CLOCK: Final = re.compile(r"\b\d{1,2}:\d{2}\b")
# How far below a "From" line the header lines may be: a long name or address can wrap.
_BLOCK_REACH: Final = 4
_WROTE: Final = re.compile(
    r"(wrote|yazdı|şunu yazdı|schrieb|a écrit|escribió|escreveu|ha scritto|написал\(а\)|написал|"
    r"написа|scrie|schreef|napisał\(a\)|napisał|skrev)\s*:\s*$",
    re.I,
)
_RULE: Final = re.compile(
    r"^\s*-{2,}\s*(original message|özgün ileti|orijinal mesaj|orijinal ileti|message d'origine|"
    r"ursprüngliche nachricht|mensaje original|messaggio originale|исходное сообщение)\s*-{2,}\s*$",
    re.I,
)
# Outlook draws a line of underscores above its header block.
_UNDERLINE: Final = re.compile(r"^\s*_{10,}\s*$")
_FORWARD: Final = re.compile(r"^\s*(fwd?|fw|İlt|ilt|tr|wg|rv|enc|i)\s*:", re.I)


def own_text(body: str, subject: str | None = None) -> str:
    """The text above the quoted history; the whole body for a forward, or when none is quoted."""
    if subject and _FORWARD.match(subject):
        return body.strip()
    lines = body.splitlines()
    cut = _history_start(lines)
    kept = [line for line in lines[:cut] if not line.lstrip().startswith(">")]
    return "\n".join(kept).strip()


def _history_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if _RULE.match(line) or _starts_block(lines, index):
            return _above_underline(lines, index)
        if _WROTE.search(line):
            # "On Tue, 1 Oct 2026 at 10:00, Name <a@b>" can wrap before "wrote:".
            if index and len(line.strip()) < 30 and lines[index - 1].strip():
                return index - 1
            return index
    return len(lines)


def _starts_block(lines: list[str], index: int) -> bool:
    """A "From" line with a subject line, or a sent line carrying a clock time, just below it: an
    order's route ("From: Istanbul" above "To: Munich") is neither."""
    first = _label(lines[index])
    if first is None or first[0] not in _FROM:
        return False
    sent = _SENT | _DATE if "@" in first[1] else _SENT
    for below in lines[index + 1 : index + 1 + _BLOCK_REACH]:
        label = _label(below)
        if label is None:
            continue
        name, value = label
        if name in _SUBJECT or (name in sent and _CLOCK.search(value)):
            return True
    return False


def _label(line: str) -> tuple[str, str] | None:
    match = _LABEL.match(line)
    return (match.group(1).casefold(), match.group(2)) if match else None


def _above_underline(lines: list[str], index: int) -> int:
    above = index - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    return above if above >= 0 and _UNDERLINE.match(lines[above]) else index
