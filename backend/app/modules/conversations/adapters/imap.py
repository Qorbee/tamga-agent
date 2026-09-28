"""Reads a mailbox over IMAP, and can do nothing else (ADR-0008).

``_ReadOnly`` refuses every command but the few that read, before anything reaches the server:
folders are opened with EXAMINE and messages fetched with BODY.PEEK, so no flag changes and nothing
is marked read, moved, deleted or sent. imaplib is synchronous: callers run a ``MailboxReader`` in
a worker thread, one call at a time.
"""

import imaplib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from app.config import Mailbox

# LOGIN and LOGOUT open and close; CAPABILITY and NOOP ask; LIST names folders; EXAMINE opens one
# read-only; UID SEARCH and UID FETCH read.
_ALLOWED: Final = frozenset({"CAPABILITY", "LOGIN", "LIST", "EXAMINE", "UID", "NOOP", "LOGOUT"})
_ALLOWED_UID: Final = frozenset({"SEARCH", "FETCH"})
# FETCH items that never set \Seen. BODY[...] and RFC822 do; their PEEK and header forms do not.
_FETCH_ITEM: Final = re.compile(r"BODY\.PEEK\[[^\]]*\](?:<[\d.]+>)?|[A-Z0-9.]+")
_SAFE_FETCH_ITEMS: Final = frozenset(
    {"UID", "INTERNALDATE", "FLAGS", "RFC822.SIZE", "ENVELOPE", "BODYSTRUCTURE"}
)
# Folders whose mail is not the company's work, or repeats another folder's.
_SKIPPED_FOLDERS: Final = frozenset({"\\TRASH", "\\JUNK", "\\DRAFTS", "\\ALL", "\\FLAGGED"})
_LIST_LINE: Final = re.compile(r'\((?P<flags>[^)]*)\) (?P<delimiter>NIL|"[^"]*") (?P<name>.+)')
_INTERNALDATE: Final = re.compile(rb'INTERNALDATE "([^"]+)"')


class WriteRefused(RuntimeError):
    """A command that could change the mailbox was about to be sent. Always a bug in Tamga."""


def check_command(name: str, args: tuple[Any, ...]) -> None:
    if name not in _ALLOWED:
        raise WriteRefused(f"IMAP {name} is not a read")
    if name != "UID":
        return
    sub = str(args[0]).upper() if args else ""
    if sub not in _ALLOWED_UID:
        raise WriteRefused(f"IMAP UID {sub} is not a read")
    if sub == "FETCH":
        items = " ".join(str(arg) for arg in args[2:]).upper()
        for item in _FETCH_ITEM.findall(items):
            if not (item.startswith("BODY.PEEK[") or item in _SAFE_FETCH_ITEMS):
                raise WriteRefused(f"IMAP FETCH {item} would mark the message read")


class _ReadOnly:
    def _command(self, name: str, *args: Any) -> str:
        check_command(name, args)
        result: str = super()._command(name, *args)  # type: ignore[misc]  # imaplib's own, untyped
        return result


class ReadOnlyIMAP4(_ReadOnly, imaplib.IMAP4):
    pass


class ReadOnlyIMAP4SSL(_ReadOnly, imaplib.IMAP4_SSL):
    pass


@dataclass(frozen=True, slots=True)
class ListedFolder:
    name: str
    # Flagged \Sent by the server: what is in it was written by the company, whatever address or
    # alias it was sent from.
    sent: bool


@dataclass(frozen=True, slots=True)
class FetchedMail:
    uid: int
    raw: bytes
    # When the server received it; the fallback for a missing or broken Date header.
    internaldate: datetime | None


class MailboxReader:
    def __init__(self, mailbox: Mailbox, *, timeout: float = 60) -> None:
        self.mailbox = mailbox
        self.timeout = timeout
        self._imap: imaplib.IMAP4 | None = None

    def open(self) -> None:
        box = self.mailbox
        imap: imaplib.IMAP4 = (
            ReadOnlyIMAP4SSL(box.host, box.port, timeout=self.timeout)
            if box.tls
            else ReadOnlyIMAP4(box.host, box.port, timeout=self.timeout)
        )
        imap.login(box.username, box.password.get_secret_value())
        self._imap = imap

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    def folders(self) -> list[ListedFolder]:
        """Every folder that holds the company's mail, Sent included; not Trash, Junk or Drafts."""
        status, lines = self._connection().list()
        _ok(status, "LIST")
        folders = []
        for line in lines:
            parsed = _parse_list_line(line)
            if parsed is None:
                continue
            flags, name = parsed
            if "\\NOSELECT" in flags or flags & _SKIPPED_FOLDERS:
                continue
            folders.append(ListedFolder(name=name, sent="\\SENT" in flags))
        return folders

    def examine(self, folder: str) -> tuple[int, int]:
        """Opens ``folder`` read-only; returns its UIDVALIDITY and UIDNEXT."""
        imap = self._connection()
        status, _ = imap.select(_quote(folder), readonly=True)
        _ok(status, f"EXAMINE {folder}")
        return _response_number(imap, "UIDVALIDITY"), _response_number(imap, "UIDNEXT", default=0)

    def uids_after(self, last_uid: int) -> list[int]:
        # `n:*` returns the highest UID even when it is below n, so filter again here.
        return [uid for uid in self._search(f"UID {last_uid + 1}:*") if uid > last_uid]

    def uids_since(self, day: date) -> list[int]:
        return self._search(f"SINCE {day.strftime('%d-%b-%Y')}")

    def fetch(self, uid: int) -> FetchedMail:
        status, data = self._connection().uid("FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE)")
        _ok(status, f"FETCH {uid}")
        for part in data:
            if isinstance(part, tuple):
                header, raw = part[0], part[1]
                return FetchedMail(uid=uid, raw=raw, internaldate=_internaldate(header))
        raise imaplib.IMAP4.error(f"FETCH {uid} returned no message")

    def _search(self, criteria: str) -> list[int]:
        status, data = self._connection().uid("SEARCH", criteria)
        _ok(status, f"SEARCH {criteria}")
        return sorted(int(uid) for uid in (data[0] or b"").split())

    def _connection(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise RuntimeError("open() the mailbox first")
        return self._imap


def _ok(status: str, what: str) -> None:
    if status != "OK":
        raise imaplib.IMAP4.error(f"{what} answered {status}")


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_list_line(line: Any) -> tuple[frozenset[str], str] | None:
    # A name with special characters arrives as a literal: (b'(\\Flags) "/" {12}', b'the name').
    if isinstance(line, tuple):
        head, literal = line[0].decode(), line[1].decode()
        match = _LIST_LINE.match(head.rsplit(" ", 1)[0] + " x")
        name = literal
    elif isinstance(line, bytes):
        match = _LIST_LINE.match(line.decode())
        name = match.group("name") if match else ""
    else:
        return None
    if match is None:
        return None
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return frozenset(flag.upper() for flag in match.group("flags").split()), name


def _response_number(imap: imaplib.IMAP4, code: str, default: int | None = None) -> int:
    _, values = imap.response(code)
    if values and values[0] is not None:
        return int(values[0])
    if default is None:
        raise imaplib.IMAP4.error(f"EXAMINE gave no {code}")
    return default


def _internaldate(header: bytes) -> datetime | None:
    match = _INTERNALDATE.search(header)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1).decode(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None
