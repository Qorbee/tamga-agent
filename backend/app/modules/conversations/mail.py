"""Reads a raw mail into the fields Tamga stores: headers, text and attachments.

Real mail is messy: broken headers, Turkish charsets, HTML-only bodies, forwarded messages nested
inside messages. Parsing never raises on a bad header or charset; it keeps what it can read. What it
returns always fits the columns it is stored in: a value the database would refuse would leave the
mail unstored, and its folder stuck behind it.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# RFC 5321: no deliverable address is longer. A longer one is not an address.
_MAX_ADDRESS = 254


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ParsedMail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    sender: str | None
    recipients: list[str]
    subject: str | None
    body_text: str
    sent_at: datetime | None
    attachments: list[Attachment] = field(default_factory=list)


UNREADABLE = ParsedMail(
    message_id=None,
    in_reply_to=None,
    references=[],
    sender=None,
    recipients=[],
    subject=None,
    body_text="",
    sent_at=None,
)


def parse_mail(raw: bytes) -> ParsedMail:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = _body_part(message)
    return ParsedMail(
        message_id=_header(message, "Message-ID"),
        in_reply_to=_header(message, "In-Reply-To"),
        references=(_header(message, "References") or "").split(),
        sender=_address(message, "From"),
        recipients=_addresses(message, "To", "Cc"),
        subject=_header(message, "Subject"),
        body_text=_text_of(body) if body is not None else "",
        sent_at=_date(message),
        attachments=_attachments(message, body),
    )


def _header(message: Message, name: str) -> str | None:
    try:
        value = message.get(name)
    except ValueError, IndexError, TypeError:  # a header the parser cannot fold
        logger.debug("unreadable %s header", name)
        return None
    if value is None:
        return None
    return _clean(str(value)).strip() or None


def _clean(text: str) -> str:
    """PostgreSQL text cannot hold NUL, which broken mail does carry."""
    return text.replace("\x00", "")


def _address(message: Message, name: str) -> str | None:
    _, address = parseaddr(_header(message, name) or "")
    return address.lower() if 0 < len(address) <= _MAX_ADDRESS else None


def _addresses(message: Message, *names: str) -> list[str]:
    values = [_header(message, name) or "" for name in names]
    return [
        address.lower() for _, address in getaddresses(values) if 0 < len(address) <= _MAX_ADDRESS
    ]


def _date(message: Message) -> datetime | None:
    value = _header(message, "Date")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _body_part(message: Message) -> Message | None:
    if isinstance(message, EmailMessage):
        return message.get_body(preferencelist=("plain", "html"))
    return None


def _text_of(part: Message) -> str:
    try:
        content = part.get_content() if isinstance(part, EmailMessage) else None
    except LookupError, ValueError:  # an unknown or lying charset
        content = None
    if not isinstance(content, str):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    content = _clean(content)
    if part.get_content_type() == "text/html":
        return _html_to_text(content)
    return content.strip()


def _attachments(message: Message, body: Message | None) -> list[Attachment]:
    """Every part with a file in it, forwarded messages' parts included: CMR scans, invoices and
    screenshots pasted inline all count."""
    found: list[Attachment] = []
    for part in message.walk():
        if part is body or part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        maintype = part.get_content_maintype()
        if (
            not filename
            and disposition != "attachment"
            and maintype not in ("image", "application")
        ):
            continue  # an alternative text or HTML body, not a file
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes) or not data:
            continue
        found.append(
            Attachment(
                filename=_clean(filename or "") or f"attachment-{len(found) + 1}",
                content_type=_clean(part.get_content_type()),
                data=data,
            )
        )
    return found


class _TextOnly(HTMLParser):
    _BLOCKS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "table"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skipping += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)
