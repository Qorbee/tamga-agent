"""What a stored file really is, and what a browser may be shown of it.

A file's declared type comes from whoever sent it: a mail client labels a PDF
``application/octet-stream``, a phone saves a PNG as ``.jpg``, and a hostile sender can label a web
page ``image/png``. So the name says what kind of file to expect, and the first bytes say what it
is: a file is shown only when they agree on the kind, under the type its bytes prove. An HTML page
named ``invoice.pdf`` is only ever a download.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final


class Preview(StrEnum):
    """How the web app shows a file; a file with none is only downloaded."""

    IMAGE = "image"
    PDF = "pdf"
    SHEET = "sheet"


PNG: Final = "image/png"
JPEG: Final = "image/jpeg"
GIF: Final = "image/gif"
WEBP: Final = "image/webp"
BMP: Final = "image/bmp"
PDF: Final = "application/pdf"
# The picture types a browser draws.
IMAGES: Final = frozenset({PNG, JPEG, GIF, WEBP, BMP})
XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS: Final = "application/vnd.ms-excel"

_DECLARED: Final = {
    PNG: PNG,
    JPEG: JPEG,
    "image/jpg": JPEG,
    "image/pjpeg": JPEG,
    GIF: GIF,
    WEBP: WEBP,
    BMP: BMP,
    PDF: PDF,
    XLSX: XLSX,
    XLS: XLS,
}
_EXTENSIONS: Final = {
    ".png": PNG,
    ".jpg": JPEG,
    ".jpeg": JPEG,
    ".gif": GIF,
    ".webp": WEBP,
    ".bmp": BMP,
    ".pdf": PDF,
    ".xlsx": XLSX,
    ".xls": XLS,
}
_PREVIEWS: Final = {
    PNG: Preview.IMAGE,
    JPEG: Preview.IMAGE,
    GIF: Preview.IMAGE,
    WEBP: Preview.IMAGE,
    BMP: Preview.IMAGE,
    PDF: Preview.PDF,
    XLSX: Preview.SHEET,
    XLS: Preview.SHEET,
}
# Shown as rows read from the file (documents/sheets.py), never drawn from its bytes.
SHEETS: Final = frozenset({XLSX, XLS})


def media_type(declared: str, filename: str) -> str | None:
    """The one type Tamga knows this file as, from its declared type, else its name."""
    known = _DECLARED.get(declared.split(";")[0].strip().lower())
    return known or _EXTENSIONS.get(PurePosixPath(filename.lower()).suffix)


def preview(declared: str, filename: str) -> Preview | None:
    known = media_type(declared, filename)
    return _PREVIEWS.get(known) if known else None


def inline_type(declared: str, filename: str, data: bytes) -> str | None:
    """The type to show this file as, or None when it may only be downloaded: its bytes must be
    an image or a PDF, of the kind its name and declared type promised."""
    promised = preview(declared, filename)
    real = sniff(data)
    return real if real is not None and _PREVIEWS[real] == promised else None


def sniff(data: bytes) -> str | None:
    """The image or PDF type the first bytes prove, if any."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return WEBP
    if data.startswith(b"BM"):
        return BMP
    # Readers accept a header anywhere in the first kilobyte, and some scanners write one late.
    if b"%PDF-" in data[:1024]:
        return PDF
    return None
