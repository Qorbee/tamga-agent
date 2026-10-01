"""The files inside an archive or an Outlook ``winmail.dat``, taken out in a locked child
(ADR-0012).

libarchive reads zip, rar (RAR5 too), 7z and tar; it is C, so it runs only through
``isolated.run``. tnefparse reads Outlook's TNEF wrapping. Each member comes back as its bytes and
the name it had in the archive, which is text to show, never a path to write to.

A reply may hold at most ``BATCH_BYTES`` of members, so a large archive is read in several runs:
each starts where the last one stopped, and carries the bytes taken out so far, so the limits hold
over the whole archive, not one run:
- at most ``MAX_MEMBERS`` files; the rest are counted, not taken out;
- at most ``MAX_TOTAL`` bytes taken out in all, and never more than ``MAX_RATIO`` times the
  archive's own size once past ``RATIO_FLOOR``: a zip bomb is refused as ``too_large``;
- what systems leave behind (``Thumbs.db``, ``.DS_Store``, ``__MACOSX/``) is skipped;
- a member locked by a password comes back with that reason and no bytes.
"""

import base64
import contextlib
import ctypes
import locale
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final

import tnefparse
from pydantic import BaseModel, Field

try:
    import libarchive
    from libarchive import ffi
except (AttributeError, OSError) as error:  # what libarchive-c raises without the C library
    raise ImportError(
        "libarchive-c needs the system's libarchive: `apt install libarchive13t64`, or on macOS "
        "`brew install libarchive` and LIBARCHIVE set to its libarchive.dylib (README)"
    ) from error

from app.platform.isolated import Json, Reason, Refused, register

MAX_MEMBERS: Final = 200
MAX_TOTAL: Final = 200 * 1024 * 1024
MAX_RATIO: Final = 100
# Below this much taken out, a high ratio is a small file that compresses well, not a bomb.
RATIO_FLOOR: Final = 1024 * 1024
# The members one run sends back, before base64: a reply stays under isolated's 64 MB.
BATCH_BYTES: Final = 32 * 1024 * 1024
# The largest size a member row holds (a PostgreSQL integer).
MAX_SIZE: Final = 2**31 - 1
# The zip default for a name written without the UTF-8 flag, when a country names no code page.
DEFAULT_CODEPAGE: Final = "cp437"

_JUNK_NAMES: Final = frozenset({"thumbs.db", ".ds_store", "desktop.ini"})
_JUNK_FOLDERS: Final = frozenset({"__macosx"})


class Kind(StrEnum):
    """What the archive is, which says how to open it. The name and the first bytes agree on it
    (filetypes.archive_kind)."""

    ZIP = "zip"
    RAR = "rar"
    SEVEN_ZIP = "7z"
    TAR = "tar"
    TNEF = "tnef"


class Request(BaseModel):
    """The hint a run gets: where to start and what the runs before it took out."""

    kind: Kind
    codepage: str = DEFAULT_CODEPAGE
    # Members already sent, by their place among the files kept.
    start: int = Field(default=0, ge=0)
    # Bytes the runs before took out.
    spent: int = Field(default=0, ge=0)


class Member(BaseModel):
    name: str = Field(max_length=1000)
    # A size a crafted header claims, for a member not taken out, is capped to what a row holds.
    size: int = Field(ge=0, le=MAX_SIZE)
    # Base64 of the bytes; null for a member that could not be taken out.
    data: str | None
    # Why it could not be: a password.
    refused: Reason | None = None


class Unpacked(BaseModel):
    """What ``unpack`` sends back, as the parent checks it."""

    members: list[Member]
    # Where the next run starts; null when this run reached the end.
    next: int | None = Field(ge=0)
    # Bytes taken out, this run and those before.
    spent: int = Field(ge=0)
    # Files past MAX_MEMBERS, counted on the last run.
    omitted: int = Field(ge=0)


def too_much(taken: int, archive_size: int) -> bool:
    """Past the total, or past the ratio once over its floor: a zip bomb. The parent checks what
    it was sent against the same bounds, since a child a crafted file took over can lie."""
    return taken > MAX_TOTAL or (taken > RATIO_FLOOR and taken > MAX_RATIO * archive_size)


@register("documents.unpack")
def unpack(data: bytes, hint: str) -> Json:
    request = Request.model_validate_json(hint)
    batch = _Batch(request, len(data))
    try:
        if request.kind == Kind.TNEF:
            _unpack_tnef(data, batch, request.codepage)
        else:
            _unpack_archive(data, request, batch)
    except _Full:
        return batch.reply(finished=False)
    return batch.reply(finished=True)


class _Full(Exception):
    """This run's reply is full; the next run carries on where it stopped."""


class _Batch:
    """Members kept in order, counted against the limits of the whole archive."""

    def __init__(self, request: Request, archive_size: int) -> None:
        self.request = request
        self.archive_size = archive_size
        self.members: list[Json] = []
        self.index = 0
        self.spent = request.spent
        self.sent = 0
        self.omitted = 0

    def wants(self, name: str) -> bool:
        """Whether this file is one to take out in this run: it is not junk, not past the cap, and
        not sent by an earlier run. A file past the cap is counted."""
        if _junk(name):
            return False
        if self.index >= MAX_MEMBERS:
            self.omitted += 1
            return False
        if self.index < self.request.start:
            self.index += 1
            return False
        return True

    def take(self, name: str, content: bytes | None, *, size: int = 0) -> None:
        if content is None:
            self.members.append(
                {"name": name, "size": min(size, MAX_SIZE), "data": None, "refused": "password"}
            )
        else:
            self.members.append(
                {"name": name, "size": len(content), "data": base64.b64encode(content).decode()}
            )
            self.sent += len(content)
        self.index += 1
        if self.sent >= BATCH_BYTES:
            raise _Full

    def count(self, amount: int) -> None:
        """Counts bytes taken out, refusing the archive past the total or the ratio."""
        self.spent += amount
        if too_much(self.spent, self.archive_size):
            raise Refused(Reason.TOO_LARGE)

    def reply(self, *, finished: bool) -> Json:
        return {
            "members": self.members,
            "next": None if finished else self.index,
            "spent": self.spent,
            "omitted": self.omitted if finished else 0,
        }


def _junk(name: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(name).parts]
    return not parts or parts[-1] in _JUNK_NAMES or any(p in _JUNK_FOLDERS for p in parts[:-1])


# libarchive's calls that libarchive-c does not wrap.
_is_encrypted = ffi.libarchive.archive_entry_is_encrypted
_is_encrypted.argtypes = [ctypes.c_void_p]
_is_encrypted.restype = ctypes.c_int

_FORMATS: Final = {Kind.ZIP: "zip", Kind.SEVEN_ZIP: "7zip", Kind.TAR: "tar"}


def _unpack_archive(data: bytes, request: Request, batch: _Batch) -> None:
    # libarchive turns a name into the process's locale; in the C locale a UTF-8 one is lost.
    with contextlib.suppress(locale.Error):
        locale.setlocale(locale.LC_CTYPE, "C.UTF-8")
    format_name = _FORMATS.get(request.kind) or (
        "rar5" if data.startswith(b"Rar!\x1a\x07\x01\x00") else "rar"
    )
    try:
        with libarchive.memory_reader(data, format_name=format_name) as archive:
            for entry in archive:
                if not entry.isreg:
                    continue
                name = _name(entry, request.codepage)
                if not batch.wants(name):
                    continue
                if _is_encrypted(entry._entry_p):
                    batch.take(name, None, size=max(entry.size or 0, 0))
                    continue
                content = bytearray()
                for block in entry.get_blocks():
                    batch.count(len(block))
                    content += block
                # An empty file holds nothing to show or read.
                if content:
                    batch.take(name, bytes(content))
    except libarchive.ArchiveError as error:
        # An archive whose headers are encrypted cannot even be listed without its password.
        locked = any(word in str(error).lower() for word in ("encrypt", "passphrase", "password"))
        raise Refused(Reason.PASSWORD if locked else Reason.DAMAGED) from error


def _name(entry: libarchive.ArchiveEntry, codepage: str) -> str:
    """The member's name as text: UTF-8 when it is, else the DOS code page a zip written on
    Windows uses. Backslashes are Windows' folder marks; a name is never a path on this disk."""
    raw: bytes | None = ffi.entry_pathname(entry._entry_p)
    text = _decode(raw, codepage) if raw is not None else (entry.pathname or "")
    return text.replace("\\", "/").strip("/")[:1000]


def _decode(raw: bytes, codepage: str) -> str:
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return raw.decode(codepage, errors="replace")


def _unpack_tnef(data: bytes, batch: _Batch, codepage: str) -> None:
    try:
        tnef = tnefparse.TNEF(data)
    except Exception as error:  # tnefparse raises its own and plain errors alike
        raise Refused(Reason.DAMAGED) from error
    for number, attachment in enumerate(tnef.attachments, start=1):
        name = _tnef_name(attachment, codepage) or f"attachment-{number}"
        content = bytes(attachment.data or b"")
        if not content or not batch.wants(name):
            continue
        batch.count(len(content))
        batch.take(name, content)


def _tnef_name(attachment: tnefparse.TNEFAttachment, codepage: str) -> str:
    """Its long name, else its short one; tnefparse decodes them by the TNEF's own code page when
    the file names one, and leaves bytes otherwise."""
    try:
        name: str | bytes = attachment.long_filename()
    except UnicodeDecodeError:
        name = attachment._name
    if isinstance(name, bytes):
        name = _decode(name, codepage)
    return name.replace("\x00", "").replace("\\", "/").strip().strip("/")[:1000]
