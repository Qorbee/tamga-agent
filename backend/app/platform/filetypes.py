"""What a stored file really is, and what a browser may be shown of it.

A file's declared type comes from whoever sent it: a mail client labels a PDF
``application/octet-stream``, a phone saves a PNG as ``.jpg``, and a hostile sender can label a web
page ``image/png``. So the name says what kind of file to expect, and the first bytes say what it
is: a file is shown only when they agree on the kind, under the type its bytes prove. An HTML page
named ``invoice.pdf`` is only ever a download.
"""

import re
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
    # Word, RTF or OpenDocument text: the PDF the converter made of it (ADR-0012).
    OFFICE = "office"
    # A web page, made safe as a mail's body is and drawn in the same sandboxed frame.
    HTML = "html"
    # XML or plain text, shown as text.
    TEXT = "text"


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
DOCX: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC: Final = "application/msword"
RTF: Final = "application/rtf"
ODT: Final = "application/vnd.oasis.opendocument.text"
HTML: Final = "text/html"
XML: Final = "application/xml"
TEXT: Final = "text/plain"

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
    DOCX: DOCX,
    DOC: DOC,
    RTF: RTF,
    "text/rtf": RTF,
    ODT: ODT,
    HTML: HTML,
    XML: XML,
    "text/xml": XML,
    TEXT: TEXT,
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
    ".docx": DOCX,
    ".doc": DOC,
    ".rtf": RTF,
    ".odt": ODT,
    ".html": HTML,
    ".htm": HTML,
    ".xml": XML,
    ".txt": TEXT,
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
    DOCX: Preview.OFFICE,
    DOC: Preview.OFFICE,
    RTF: Preview.OFFICE,
    ODT: Preview.OFFICE,
    HTML: Preview.HTML,
    XML: Preview.TEXT,
    TEXT: Preview.TEXT,
}
# Shown as rows read from the file (documents/sheets.py), never drawn from its bytes.
SHEETS: Final = frozenset({XLSX, XLS})
# Converted to PDF (platform/converter.py), by the extension that picks LibreOffice's reader.
OFFICE: Final = frozenset({DOCX, DOC, RTF, ODT})
_OLE2: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OFFICE_EXTENSIONS: Final = {DOCX: ".docx", DOC: ".doc", RTF: ".rtf", ODT: ".odt"}
# Shown as text: an HTML page after the sanitizer, the rest as it is.
TEXTS: Final = frozenset({HTML, XML, TEXT})
# A text file's byte-order marks, and where its XML prolog or HTML names its encoding:
# `<?xml … encoding="…"?>`, `<meta charset="…">`, `<meta … content="text/html; charset=…">`.
_MARKS: Final = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_DECLARED_CHARSET: Final = re.compile(r"""charset\s*=\s*["']?([A-Za-z0-9_.:-]{1,40})""", re.I)
_NAMED_ENCODING: Final = re.compile(
    rb"""(?:encoding|charset)\s*=\s*["']?([A-Za-z0-9_.:-]{1,40})""", re.I
)

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
    """The one type Tamga knows this file as, from its declared type, else its name. A declared
    text type yields to a name Tamga knows: mail clients call what they do not know text."""
    known = _DECLARED.get(declared.split(";")[0].strip().lower())
    named = _EXTENSIONS.get(PurePosixPath(filename.lower()).suffix)
    if known in TEXTS and named is not None:
        return named
    return known or named


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


def office_extension(media: str, data: bytes) -> str | None:
    """The extension to convert an office file under, as its first bytes prove it: Word 97's
    container, RTF, or a zip (Word's or OpenDocument's, as the name promised). None when they are
    none of these: LibreOffice would open any bytes as text, and convert them."""
    if data.startswith(_OLE2):
        return ".doc"
    if data.startswith(b"{\\rtf"):
        return ".rtf"
    if data.startswith(b"PK\x03\x04"):
        return ".odt" if media == ODT else ".docx"
    return None


def charset(declared: str) -> str | None:
    """The charset a declared type names (``text/plain; charset=windows-1254``), if any."""
    found = _DECLARED_CHARSET.search(declared)
    return found.group(1) if found else None


def decode_text(data: bytes, declared: str | None = None) -> str | None:
    """A text file's content: by its byte-order mark, else as UTF-8, else in the charset its
    declared type names (``declared``), else in the encoding its XML prolog or HTML ``meta``
    names, else as UTF-8 with what does not decode replaced. None when it is not text: a NUL
    byte in it."""
    for mark, encoding in _MARKS:
        if data.startswith(mark):
            return data[len(mark) :].decode(encoding, errors="replace").replace("\x00", "")
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    named = _NAMED_ENCODING.search(data[:2048])
    for candidate in (declared, named.group(1).decode("ascii") if named else None):
        if candidate:
            try:
                return data.decode(candidate, errors="replace")
            except LookupError:
                pass
    return data.decode("utf-8", errors="replace")
