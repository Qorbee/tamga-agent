"""A mail's HTML body made safe to show: what the web app draws for a mail, in a sandboxed frame.

The HTML is from outside and is data, never instructions (root AGENTS.md, invariant 3): nothing in
it may run, reach the network or leave the frame. Three layers hold that:

- nh3 (the Rust ammonia sanitizer) keeps an allowlist of formatting tags and attributes, so no
  script, form, frame, object or event handler survives, and links only to http(s), mailto and tel;
- an image the mail carries, which its HTML names by Content-ID (``cid:``, RFC 2392), stays a
  ``cid:`` reference when its bytes prove to be an image; the web app draws it from the stored
  file, so a logo repeated through a thread loads once. Images on the web are dropped and counted:
  fetching one tells the sender the mail was read (a tracking pixel), and a company in shadow mode
  signals nothing (invariant 6);
- the web app's frame runs no script, and its Content-Security-Policy loads nothing but images it
  holds itself (data: and blob:), which also covers any URL in the mail's own CSS.

The mail's ``<style>`` is kept, since mail clients lay a body out with it; inside that frame it can
only style the mail.
"""

import hashlib
from dataclasses import dataclass
from email.message import Message
from typing import Final
from urllib.parse import unquote

import nh3

from app.modules.conversations.mail import (
    content_id,
    html_part,
    own_parts,
    read_message,
    text_of_part,
)
from app.platform import filetypes

_TAGS: Final = frozenset(
    {
        "a", "abbr", "address", "b", "bdi", "bdo", "big", "blockquote", "br", "caption", "center",
        "cite", "code", "col", "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "font",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p",
        "pre", "q", "s", "small", "span", "strike", "strong", "style", "sub", "sup", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "wbr",
    }
)  # fmt: skip
# Removed with their text: what would otherwise leave stray words (a page title, a form's options).
_DROPPED: Final = frozenset(
    {
        "applet", "button", "embed", "frame", "frameset", "iframe", "math", "noscript", "object",
        "script", "select", "svg", "template", "textarea", "title",
    }
)  # fmt: skip
_ATTRIBUTES: Final = {
    "*": {
        "align", "bgcolor", "class", "dir", "height", "id", "lang", "style", "title", "valign",
        "width",
    },
    "a": {"href", "name"},
    "blockquote": {"type"},
    "col": {"span"},
    "colgroup": {"span"},
    "font": {"color", "face", "size"},
    "img": {"alt", "border", "src"},
    "li": {"value"},
    "ol": {"start", "type"},
    "table": {"border", "cellpadding", "cellspacing"},
    "td": {"colspan", "nowrap", "rowspan"},
    "th": {"colspan", "nowrap", "rowspan", "scope"},
    "ul": {"type"},
}  # fmt: skip
# Layout and type only: nothing that places the mail over the page around it.
_STYLE: Final = frozenset(
    {
        "background", "background-color", "border", "border-bottom", "border-bottom-color",
        "border-bottom-style", "border-bottom-width", "border-collapse", "border-color",
        "border-left", "border-left-color", "border-left-style", "border-left-width",
        "border-radius", "border-right", "border-right-color", "border-right-style",
        "border-right-width", "border-spacing", "border-style", "border-top", "border-top-color",
        "border-top-style", "border-top-width", "border-width", "clear", "color", "direction",
        "display", "float", "font", "font-family", "font-size", "font-style", "font-variant",
        "font-weight", "height", "letter-spacing", "line-height", "list-style-type", "margin",
        "margin-bottom", "margin-left", "margin-right", "margin-top", "max-height", "max-width",
        "min-height", "min-width", "opacity", "overflow", "padding", "padding-bottom",
        "padding-left", "padding-right", "padding-top", "table-layout", "text-align",
        "text-decoration", "text-indent", "text-transform", "vertical-align", "visibility",
        "white-space", "width", "word-break", "word-spacing", "word-wrap", "overflow-wrap",
    }
)  # fmt: skip
_URL_SCHEMES: Final = frozenset({"http", "https", "mailto", "tel", "cid", "data"})
_LINK_SCHEMES: Final = frozenset({"http", "https", "mailto", "tel"})


@dataclass(frozen=True, slots=True)
class MailHtml:
    html: str
    # Images on the web the mail pointed at, none of which was fetched.
    remote_images: int
    # The images the HTML shows by `cid:`, each by the SHA-256 of its bytes (a stored file's key).
    images: dict[str, bytes]


def render(raw: bytes) -> MailHtml | None:
    """The mail's HTML body, sanitized, or None when it has none (show its text instead). Parsing
    and sanitizing are synchronous: run it in a worker thread."""
    message = read_message(raw)
    part = html_part(message)
    if part is None:
        return None
    images = _Images(_carried(message))
    html = nh3.clean(
        text_of_part(part),
        tags=set(_TAGS),
        clean_content_tags=set(_DROPPED),
        attributes={tag: set(names) for tag, names in _ATTRIBUTES.items()},
        attribute_filter=images.attribute,
        url_schemes=set(_URL_SCHEMES),
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={"a": {"target": "_blank"}},
        filter_style_properties=set(_STYLE),
    )
    return MailHtml(html=html, remote_images=images.remote, images=images.shown)


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


class _Images:
    def __init__(self, carried: dict[str, bytes]) -> None:
        self._carried = carried
        self.remote = 0
        self.shown: dict[str, bytes] = {}

    def attribute(self, tag: str, attribute: str, value: str) -> str | None:
        """Called by nh3 for each attribute that passed the allowlist and the URL schemes; None
        drops it."""
        if attribute == "href":
            return value if _scheme(value) in _LINK_SCHEMES else None
        if tag != "img" or attribute != "src":
            return value
        scheme = _scheme(value)
        if scheme == "cid":
            cid = unquote(value[4:]).strip("<> ").lower()
            if cid not in self._carried:
                return None
            self.shown[cid] = self._carried[cid]
            # Written as the web app looks it up: decoded, without brackets, lower case.
            return f"cid:{cid}"
        if scheme == "data":
            media = value[5:].split(";", 1)[0].split(",", 1)[0].strip().lower()
            return value if media in filetypes.IMAGES else None
        if scheme in ("http", "https"):
            self.remote += 1
        return None


def _scheme(url: str) -> str:
    head, colon, _ = url.partition(":")
    return head.strip().lower() if colon else ""
