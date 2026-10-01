"""A mail's HTML body made safe to show: what the web app draws for a mail, in a sandboxed frame.

The sanitizing is ``platform/html.py``'s, which a mail shares with an HTML file. What is the mail's
own: the images it carries by Content-ID (``cid:``, RFC 2392), kept when their bytes prove to be
images, so the web app draws each from the stored file and a logo repeated through a thread loads
once.
"""

import hashlib
from email.message import Message

from app.modules.conversations.mail import (
    content_id,
    html_part,
    own_parts,
    read_message,
    text_of_part,
)
from app.platform import filetypes
from app.platform.html import SafeHtml, sanitize


def render(raw: bytes) -> SafeHtml | None:
    """The mail's HTML body, sanitized, or None when it has none (show its text instead). Parsing
    and sanitizing are synchronous: run it in a worker thread."""
    message = read_message(raw)
    part = html_part(message)
    if part is None:
        return None
    return sanitize(text_of_part(part), _carried(message))


def _carried(message: Message) -> dict[str, bytes]:
    """The images this mail carries with a Content-ID, by the SHA-256 of their bytes. The type
    comes from the bytes, never from what the part claims."""
    found: dict[str, bytes] = {}
    for part in own_parts(message):
        cid = content_id(part)
        if cid is None or cid in found:
            continue
        data = part.get_payload(decode=True)
        if isinstance(data, bytes) and filetypes.sniff(data) in filetypes.IMAGES:
            found[cid] = hashlib.sha256(data).digest()
    return found
