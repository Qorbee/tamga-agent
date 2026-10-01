"""The body Outlook puts inside a ``winmail.dat``: compressed rich text, and the HTML wrapped in it.

A mail sent as "rich text" carries only a bare text part beside its ``winmail.dat``. What the
sender wrote and laid out is inside that file, as RTF compressed with Microsoft's LZFu
([MS-OXRTFCP]). When the sender wrote HTML, the RTF is a wrapper around it ([MS-OXRTFEX]), and
the HTML comes back out whole; when they wrote rich text, there is no HTML to find.

Both functions are pure, over bytes from outside: they run only in the locked child (ADR-0012).
"""

import codecs
import struct
from dataclasses import dataclass, replace
from typing import Final

# The most rich text one body may hold, compressed or not.
MAX_RTF: Final = 16 * 1024 * 1024
_COMPRESSED: Final = 0x75465A4C  # "LZFu"
_STORED: Final = 0x414C454D  # "MELA": the RTF as it is
_WINDOW: Final = 4096
# What the window holds before the first byte: the words nearly every RTF file starts with.
_PRESET: Final = (
    b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
    b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0"
    b"\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx"
)


def decompress(data: bytes) -> bytes:
    """The RTF inside a compressed-RTF property. Raises ValueError for anything else."""
    if len(data) < 16:
        raise ValueError("too short for compressed RTF")
    compressed_size, raw_size, kind, _crc = struct.unpack_from("<IIII", data)
    if raw_size > MAX_RTF:
        raise ValueError("more rich text than one body may hold")
    body = data[16 : compressed_size + 4]
    if kind == _STORED:
        return body[:raw_size]
    if kind != _COMPRESSED:
        raise ValueError("not compressed RTF")

    window = bytearray(_WINDOW)
    window[: len(_PRESET)] = _PRESET
    write = len(_PRESET)
    out = bytearray()
    at = 0
    while at < len(body) and len(out) < raw_size:
        control = body[at]
        at += 1
        for bit in range(8):
            if control >> bit & 1:
                if at + 2 > len(body):
                    return bytes(out)
                reference = body[at] << 8 | body[at + 1]
                at += 2
                offset = reference >> 4
                # A reference to where the next byte would go ends the stream.
                if offset == write:
                    return bytes(out)
                for _ in range((reference & 0xF) + 2):
                    out.append(window[offset])
                    window[write] = window[offset]
                    offset = (offset + 1) % _WINDOW
                    write = (write + 1) % _WINDOW
            else:
                if at >= len(body):
                    return bytes(out)
                out.append(body[at])
                window[write] = body[at]
                write = (write + 1) % _WINDOW
                at += 1
    return bytes(out[:raw_size])


# Groups whose text is the RTF's own bookkeeping, never the message.
_TABLES: Final = frozenset({"fonttbl", "colortbl", "stylesheet", "info", "pict", "object"})
_SYMBOLS: Final = {
    "par": "\r\n",
    "line": "\r\n",
    "tab": "\t",
    "lquote": "\u2018",
    "rquote": "\u2019",
    "ldblquote": "\u201c",
    "rdblquote": "\u201d",
    "bullet": "\u2022",
    "endash": "\u2013",
    "emdash": "\u2014",
}
_LETTERS: Final = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_DIGITS: Final = frozenset(b"0123456789")


@dataclass(frozen=True, slots=True)
class _Group:
    # Inside a group that holds none of the message: a table, or a destination other than
    # ``{\*\htmltag …}``, which holds a piece of the HTML itself.
    skipped: bool = False
    # Between ``\htmlrtf`` and ``\htmlrtf0``: what only an RTF reader draws.
    rtf_only: bool = False
    # How many characters after a ``\u`` stand in for it, for a reader without Unicode.
    stand_ins: int = 1

    @property
    def shown(self) -> bool:
        return not self.skipped and not self.rtf_only


def html_inside(rtf: bytes) -> str | None:
    """The HTML a mail client wrapped in this RTF, or None when the RTF wraps none."""
    if b"\\fromhtml1" not in rtf[:2048]:
        return None
    out: list[str] = []
    # Bytes waiting to be read in the document's code page, so a two-byte character stays whole.
    waiting = bytearray()
    codec = "cp1252"
    group = _Group()
    stack: list[_Group] = []
    starred = False
    to_skip = 0

    def flush() -> None:
        if waiting:
            out.append(waiting.decode(codec, errors="replace"))
            waiting.clear()

    at, end = 0, len(rtf)
    while at < end:
        byte = rtf[at]
        if byte == 0x7B:  # {
            stack.append(group)
            starred, to_skip = False, 0
            at += 1
        elif byte == 0x7D:  # }
            if not stack:
                break
            group = stack.pop()
            starred, to_skip = False, 0
            at += 1
        elif byte != 0x5C:  # not a backslash: text
            at += 1
            if byte in (0x0D, 0x0A):
                continue  # a line break in the RTF's own text means nothing
            if to_skip:
                to_skip -= 1
            elif group.shown:
                waiting.append(byte)
        elif at + 1 >= end:
            break
        elif rtf[at + 1] in _LETTERS:
            start = at + 1
            at = start
            while at < end and rtf[at] in _LETTERS:
                at += 1
            word = rtf[start:at].decode("ascii")
            start = at
            if at < end and rtf[at] == 0x2D:  # -
                at += 1
            while at < end and rtf[at] in _DIGITS:
                at += 1
            number = int(rtf[start:at]) if at > start and rtf[at - 1] in _DIGITS else None
            if at < end and rtf[at] == 0x20:
                at += 1  # the one space that ends a control word is not text

            if word == "bin" and number is not None and number > 0:
                at += number  # raw bytes, which may hold braces
            elif starred:
                starred = False
                if word != "htmltag":
                    group = replace(group, skipped=True)
            elif word in _TABLES:
                group = replace(group, skipped=True)
            elif word == "htmlrtf":
                group = replace(group, rtf_only=number != 0)
            elif word == "ansicpg" and number is not None:
                codec = _codec(number)
            elif word == "uc" and number is not None:
                group = replace(group, stand_ins=max(number, 0))
            elif word == "u" and number is not None:
                if group.shown:
                    flush()
                    out.append(chr(number % 0x10000))
                to_skip = group.stand_ins
            elif word in _SYMBOLS and group.shown:
                flush()
                out.append(_SYMBOLS[word])
        else:
            symbol = rtf[at + 1]
            at += 2
            if symbol == 0x2A:  # \* : the group may be skipped by a reader that does not know it
                starred = True
            elif symbol == 0x27:  # \'hh : one byte in the code page
                pair = rtf[at : at + 2]
                at += 2
                if to_skip:
                    to_skip -= 1
                elif (
                    group.shown
                    and len(pair) == 2
                    and all(c in b"0123456789abcdefABCDEF" for c in pair)
                ):
                    waiting.append(int(pair, 16))
            elif to_skip and symbol in b"{}\\":
                to_skip -= 1
            elif group.shown:
                if symbol in b"{}\\":
                    waiting.append(symbol)
                elif symbol in b"\r\n":
                    flush()
                    out.append("\r\n")
                elif symbol == 0x7E:  # \~ : a space that does not break
                    flush()
                    out.append("\u00a0")
                elif symbol == 0x5F:  # \_ : a hyphen that does not break
                    waiting.append(0x2D)
    flush()
    # A character outside the first plane arrives as two \u halves: join them.
    html = "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace").strip()
    return html or None


def _codec(code_page: int) -> str:
    name = f"cp{code_page}"
    try:
        codecs.lookup(name)
    except LookupError:
        return "cp1252"
    return name
