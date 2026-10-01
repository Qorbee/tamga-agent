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
    # A zip, rar, 7z or tar, or Outlook's winmail.dat: a folder of the files taken out of it.
    ARCHIVE = "archive"


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

# Archives, by the name's extension: a declared zip type alone is not one, since mail clients label
# .xlsx and .docx files (zips inside) that way too.
ZIP: Final = "application/zip"
RAR: Final = "application/vnd.rar"
SEVEN_ZIP: Final = "application/x-7z-compressed"
TAR: Final = "application/x-tar"
# Outlook's wrapping of a mail's files, sent as winmail.dat.
TNEF: Final = "application/vnd.ms-tnef"
_ARCHIVE_EXTENSIONS: Final = {
    ".zip": ZIP,
    ".rar": RAR,
    ".7z": SEVEN_ZIP,
    ".tar": TAR,
    ".tgz": TAR,
    ".tar.gz": TAR,
}
_ARCHIVE_DECLARED: Final = {
    ZIP: ZIP,
    "application/x-zip-compressed": ZIP,
    RAR: RAR,
    "application/x-rar-compressed": RAR,
    SEVEN_ZIP: SEVEN_ZIP,
    TAR: TAR,
}
_TNEF_DECLARED: Final = frozenset({TNEF, "application/ms-tnef"})
# Extensions that say nothing of what a file is, so its declared type may: a mail client's
# ATT00001.bin. Any other one says what it is (a .pptx is a zip inside, and not an archive).
_NO_KIND: Final = frozenset({"", ".bin", ".dat", ".tmp"})
# The first bytes each archive starts with; a tar says so at byte 257, a gzipped one is gzip's.
_ARCHIVE_KINDS: Final = {ZIP: "zip", RAR: "rar", SEVEN_ZIP: "7z", TAR: "tar", TNEF: "tnef"}


def media_type(declared: str, filename: str) -> str | None:
    """The one type Tamga knows this file as, from its declared type, else its name."""
    known = _DECLARED.get(declared.split(";")[0].strip().lower())
    return known or _EXTENSIONS.get(PurePosixPath(filename.lower()).suffix)


def preview(declared: str, filename: str) -> Preview | None:
    if archive_type(declared, filename) is not None:
        return Preview.ARCHIVE
    known = media_type(declared, filename)
    return _PREVIEWS.get(known) if known else None


def archive_type(declared: str, filename: str) -> str | None:
    """The archive a file is expected to be: by its extension (``.zip``, ``.rar``, ``.7z``,
    ``.tar``, ``.tgz``), a name whose extension says nothing (none, ``.bin``) and an archive's
    declared type, or Outlook's ``winmail.dat`` and TNEF type. A zip-typed ``.xlsx`` is not
    one."""
    kind = declared.split(";")[0].strip().lower()
    name = PurePosixPath(filename.lower().replace("\\", "/")).name
    if kind in _TNEF_DECLARED or name == "winmail.dat":
        return TNEF
    if name.endswith(".tar.gz"):
        return TAR
    suffix = PurePosixPath(name).suffix
    if suffix not in _NO_KIND:
        return _ARCHIVE_EXTENSIONS.get(suffix)
    return _ARCHIVE_DECLARED.get(kind)


def extension(filename: str) -> str:
    """The extension that says what a file is, as written: ``.pdf``, and ``.tar.gz`` whole."""
    if filename.lower().endswith(".tar.gz"):
        return filename[-len(".tar.gz") :]
    return PurePosixPath(filename).suffix


def archive_kind(declared: str, filename: str, data: bytes) -> str | None:
    """How to open an archive (``zip``, ``rar``, ``7z``, ``tar``, ``tnef``) when its first bytes
    are the archive its name promised; None when they are not, or it promised none."""
    promised = archive_type(declared, filename)
    if promised is None:
        return None
    proven = {
        ZIP: data.startswith((b"PK\x03\x04", b"PK\x05\x06")),
        RAR: data.startswith(b"Rar!\x1a\x07"),
        SEVEN_ZIP: data.startswith(b"7z\xbc\xaf\x27\x1c"),
        TAR: data[257:262] == b"ustar" or data.startswith(b"\x1f\x8b"),
        TNEF: data.startswith(b"\x78\x9f\x3e\x22"),
    }[promised]
    return _ARCHIVE_KINDS[promised] if proven else None


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
