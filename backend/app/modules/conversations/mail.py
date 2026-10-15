"""Reads a raw mail into the fields Tamga stores: headers, text and attachments.

Real mail is messy: broken headers, Turkish charsets, HTML-only bodies, forwarded messages nested
inside messages. Parsing never raises on a bad header or charset; it keeps what it can read. What it
returns always fits the columns it is stored in: a value the database would refuse would leave the
mail unstored, and its folder stuck behind it.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Final
from urllib.parse import unquote

from app.platform import filetypes
from app.platform.html import shown_text

logger = logging.getLogger(__name__)

# RFC 5321: no deliverable address is longer. A longer one is not an address.
_MAX_ADDRESS = 254
_MAX_NAME = 200
# A line that tells how to reach its writer: a mail or web address, a phone number written
# internationally or under its label. A date or an order number is none.
_CONTACT: Final = re.compile(
    r"\S+@\S+\.\w{2,}|(?:www\.|https?://)\S+|\+\d[\d\s().-]{7,}\d|"
    r"\b(?:tel|phone|telefon|gsm|mobile|mob|cep|fax|faks|[tmpf])\b\.?\s*:?\s*\(?\+?\d",
    re.I,
)
# How many lines above a picture its writer's contact lines may be, how many a signature has
# (a phone and a mail or web address: one alone can be a driver's number in a message), and how
# many words a line of the writer's own message has more than (a signature's lines have fewer).
_SIGNATURE_REACH: Final = 8
_SIGNATURE_CONTACTS: Final = 2
_SENTENCE_WORDS: Final = 8


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes
    # Shown inside the HTML body (a signature logo, a pasted screenshot), not attached beside it.
    inline: bool = False
    # Shown below its writer's contact lines: the sender's signature, never a job's picture.
    signature: bool = False


@dataclass(frozen=True, slots=True)
class ParsedMail:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    sender: str | None
    # The name the sender's client wrote beside the address ("Ayşe Kaya"), if any.
    sender_name: str | None
    recipients: list[str]
    subject: str | None
    body_text: str
    sent_at: datetime | None
    attachments: list[Attachment] = field(default_factory=list)
    # Sent to a list, not written to this reader: its headers carry what bulk senders add.
    bulk: bool = False
    # Of the recipients, those only copied (Cc).
    copied: list[str] = field(default_factory=list)


UNREADABLE = ParsedMail(
    message_id=None,
    in_reply_to=None,
    references=[],
    sender=None,
    sender_name=None,
    recipients=[],
    subject=None,
    body_text="",
    sent_at=None,
)


def read_message(raw: bytes) -> Message:
    return BytesParser(policy=policy.default).parsebytes(raw)


def parse_mail(raw: bytes) -> ParsedMail:
    message = read_message(raw)
    body = _body_part(message)
    return ParsedMail(
        message_id=_header(message, "Message-ID"),
        in_reply_to=_header(message, "In-Reply-To"),
        references=(_header(message, "References") or "").split(),
        sender=_address(message, "From"),
        sender_name=_name(message, "From"),
        recipients=_addresses(message, "To", "Cc"),
        copied=_addresses(message, "Cc"),
        subject=_header(message, "Subject"),
        body_text=_text_of(body) if body is not None else "",
        sent_at=_date(message),
        attachments=_attachments(message, body),
        bulk=_bulk(message),
    )


def _bulk(message: Message) -> bool:
    """Whether the mail went to a list: an unsubscribe or list header, which the large mail
    providers require of bulk senders, a bulk precedence, or a campaign's feedback id. A person's
    mail client writes none of them, and neither does a system that notifies one customer of one
    event (an invoice, a payment)."""
    if _header(message, "List-Unsubscribe") or _header(message, "List-Id"):
        return True
    if (_header(message, "Precedence") or "").lower() in ("bulk", "list", "junk"):
        return True
    return _header(message, "Feedback-ID") is not None


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


def _name(message: Message, name: str) -> str | None:
    display, _ = parseaddr(_header(message, name) or "")
    display = " ".join(display.split()).strip("'\" ")
    return display[:_MAX_NAME] or None


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


def html_part(message: Message) -> Message | None:
    """The HTML the sender's client wrote, when it wrote one."""
    if isinstance(message, EmailMessage):
        return message.get_body(preferencelist=("html",))
    return None


def text_of_part(part: Message) -> str:
    """A text part's content, whatever its charset claims."""
    try:
        content = part.get_content() if isinstance(part, EmailMessage) else None
    except LookupError, ValueError:  # an unknown or lying charset
        content = None
    if not isinstance(content, str):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    return _clean(content)


def content_id(part: Message) -> str | None:
    """A part's Content-ID as a `cid:` reference names it: no angle brackets, any case."""
    value = _header(part, "Content-ID")
    cid = value.strip("<> ").lower() if value else ""
    return cid or None


def shown_images(html: str) -> set[str]:
    """The Content-IDs an HTML body draws as pictures: `<img src="cid:…">` (RFC 2392), decoded,
    without brackets, lower case. A `cid:` anywhere else (a link, a stylesheet, the text) draws
    nothing, so the part it names stays a file beside the text."""
    parser = _ImageSources()
    parser.feed(html)
    return {cid for _, cid in parser.drawn}


def signature_images(html: str) -> set[str]:
    """The Content-IDs an HTML body draws below its writer's contact lines: two lines with a mail
    or web address or a phone number among the lines just above the picture, and no sentence of
    the writer's own between them. That is a signature's banner or logo, however a model would
    read its picture; a screenshot pasted above the sign-off has no contact line above it, and a
    photo under one driver's number has only one."""
    parser = _ImageSources()
    parser.feed(html)
    found: set[str] = set()
    for at, cid in parser.drawn:
        contacts = 0
        for line in reversed(parser.lines[max(0, at - _SIGNATURE_REACH) : at]):
            if len(line.split()) > _SENTENCE_WORDS and not _CONTACT.search(line):
                break
            contacts += bool(_CONTACT.search(line))
            if contacts >= _SIGNATURE_CONTACTS:
                found.add(cid)
                break
    return found


class _ImageSources(HTMLParser):
    """The pictures an HTML body draws by Content-ID, each with how many lines of text came
    before it."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.drawn: list[tuple[int, str]] = []

    def handle_data(self, data: str) -> None:
        self.lines.extend(line for each in data.splitlines() if (line := " ".join(each.split())))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src" and value and value.strip().lower().startswith("cid:"):
                cid = unquote(value.strip()[4:]).strip("<> ").lower()
                self.drawn.append((len(self.lines), cid))


def own_parts(message: Message) -> Iterator[Message]:
    """The mail's own leaf parts, in order. A mail forwarded inside it is one part, not entered:
    its body is not the one shown."""
    if message.get_content_maintype() != "multipart":
        yield message
        return
    children = message.get_payload()
    for child in children if isinstance(children, list) else []:
        if isinstance(child, Message):
            yield from own_parts(child)


def _text_of(part: Message) -> str:
    content = text_of_part(part)
    if part.get_content_type() == "text/html":
        return shown_text(content).text
    return content.strip()


def _attachments(message: Message, body: Message | None) -> list[Attachment]:
    """Every part with a file in it, forwarded messages' parts included: CMR scans, invoices and
    screenshots pasted inline all count. A picture the HTML body draws by its Content-ID, whose
    bytes prove it is one, is marked inline: shown where the sender put it rather than beside the
    text. The file is still stored, as a pasted screenshot can be the document itself."""
    html = html_part(message)
    shown = shown_images(text_of_part(html)) if html is not None else set()
    signed = signature_images(text_of_part(html)) if html is not None else set()
    own = {id(part) for part in own_parts(message)}
    found: list[Attachment] = []
    for part in message.walk():
        if part is body or part is html or part.is_multipart():
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
        cid = content_id(part)
        inline = cid in shown and id(part) in own and filetypes.sniff(data) in filetypes.IMAGES
        found.append(
            Attachment(
                filename=_clean(filename or "") or f"attachment-{len(found) + 1}",
                content_type=_clean(_declared_type(part)),
                data=data,
                inline=inline,
                signature=inline and cid in signed,
            )
        )
    return found


def _declared_type(part: Message) -> str:
    """A part's type, with the charset a text file was written in: a legacy file names it only
    there."""
    charset = part.get_content_charset()
    kind = part.get_content_type()
    return f"{kind}; charset={charset}" if charset else kind
