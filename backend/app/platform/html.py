"""HTML from outside made safe to show: a mail's body, an HTML file. The web app draws it in a
sandboxed frame.

The HTML is data, never instructions (root AGENTS.md, invariant 3): nothing in it may run, reach
the network or leave the frame. Three layers hold that:

- nh3 (the Rust ammonia sanitizer) keeps an allowlist of formatting tags and attributes, so no
  script, form, frame, object or event handler survives, and links only to http(s), mailto and tel;
- an image a mail carries, which its HTML names by Content-ID (``cid:``, RFC 2392), stays a
  ``cid:`` reference when its bytes prove to be an image (``carried``); the web app draws it from
  the stored file. Any other ``cid:`` goes. Images on the web keep their address and are counted,
  but Tamga never fetches one: fetching tells the sender the page was read (a tracking pixel), and
  a company in shadow mode signals nothing on its own (invariant 6). The person reading may load
  them, as they may follow a link;
- the web app's frame runs no script, and its Content-Security-Policy loads nothing but images it
  holds itself (data: and blob:), which also covers any URL in the page's own CSS, until that
  person asks for the pictures.

The page's CSS is kept whole, in its ``<style>`` and in each ``style`` attribute, as are its
``<body>``'s attributes (its background and base style), since mail clients lay a body out with
them: inside that frame they can only style the page.

Parsing and sanitizing are synchronous: run them in a worker thread.
"""

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Final
from urllib.parse import unquote

import nh3

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
# What lays a page out or colours it: what can hide text from a reader.
_STYLING: Final = frozenset({"style", "class", "id", "bgcolor", "color", "height", "width"})
_URL_SCHEMES: Final = frozenset({"http", "https", "mailto", "tel", "cid", "data"})
_LINK_SCHEMES: Final = frozenset({"http", "https", "mailto", "tel"})
_BODY_TAG: Final = re.compile(r"<body\b[^>]*>", re.I)
# A picture the mail's CSS draws from the web, inline or in its `<style>`.
_CSS_PICTURE: Final = re.compile(r"background(?:-image)?\s*:[^;}\"]*url\(\s*['\"]?\s*https?:", re.I)


@dataclass(frozen=True, slots=True)
class SafeHtml:
    html: str
    # Images on the web the page points at, which the web app loads only when asked.
    remote_images: int
    # The images it shows by `cid:`, each by the SHA-256 of its bytes (a stored file's key).
    images: dict[str, bytes]


def sanitize(
    source: str, carried: dict[str, bytes] | None = None, *, styled: bool = True
) -> SafeHtml:
    """``source`` made safe to show. ``carried``: the images a mail carries, by Content-ID, each
    by the SHA-256 of its bytes; a `cid:` naming none of them goes.

    Without ``styled``, its stylesheets, ``style`` attributes and colours go too, so nothing in
    it can be hidden from the person reading it (an HTML file, whose text is read for facts:
    ADR-0013). A mail keeps them, as mail clients lay it out with them."""
    images = _Images(carried or {})
    html = _clean(source, images, styled=styled)
    remote = images.remote + len(_CSS_PICTURE.findall(html))
    if body := _body_attributes(source, images, styled=styled):
        html = f"<body{body}>{html}</body>"
    return SafeHtml(html=html, remote_images=remote, images=images.shown)


def _clean(html: str, images: _Images, *, styled: bool) -> str:
    tags, dropped, attributes = set(_TAGS), set(_DROPPED), dict(_ATTRIBUTES)
    if not styled:
        tags -= {"style"}
        dropped |= {"style"}
        attributes = {tag: names - _STYLING for tag, names in attributes.items()}
    return nh3.clean(
        html,
        tags=tags,
        clean_content_tags=dropped,
        attributes={tag: set(names) for tag, names in attributes.items()},
        attribute_filter=images.attribute,
        url_schemes=set(_URL_SCHEMES),
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def _body_attributes(html: str, images: _Images, *, styled: bool) -> str:
    """The `<body>`'s attributes, sanitized as any element's, as markup (` style="…"`), or "".
    nh3 drops the tag and them with it; the web app puts them back on its frame's body."""
    tag = _BODY_TAG.search(html)
    found = _BodyTag()
    found.feed(tag.group(0) if tag else "")
    if not found.attributes:
        return ""
    given = "".join(
        f' {name}="{escape(value or "", quote=True)}"' for name, value in found.attributes
    )
    kept = re.fullmatch(r"<div([^>]*)></div>", _clean(f"<div{given}></div>", images, styled=styled))
    return kept.group(1) if kept else ""


class _BodyTag(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attributes = attrs


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
            return value
        return None


def _scheme(url: str) -> str:
    head, colon, _ = url.partition(":")
    return head.strip().lower() if colon else ""


@dataclass(frozen=True, slots=True)
class ShownText:
    text: str
    # Elements with text that an inline style hides (`display:none`, `visibility:hidden`), left
    # out: what a person does not see reaches no model (ADR-0013).
    hidden: int


def shown_text(html: str) -> ShownText:
    """The text a page shows, a line per block. A stylesheet's rules are not applied: only what
    an element's own ``style`` hides is left out."""
    parser = _TextOnly()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return ShownText(text="\n".join(line for line in lines if line), hidden=parser.hidden)


class _TextOnly(HTMLParser):
    _BLOCKS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "table"})
    # Elements with no end tag: one never opens a stretch to skip.
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0
        self._skipping = 0
        # The element hiding what follows, and how many of its name are open inside it.
        self._hiding: str | None = None
        self._depth = 0
        self._counted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hiding is not None:
            self._depth += tag == self._hiding
            return
        if tag not in self._VOID and _hides(dict(attrs).get("style")):
            self._hiding, self._depth, self._counted = tag, 1, False
        elif tag in ("script", "style"):
            self._skipping += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._hiding is not None:
            if tag == self._hiding:
                self._depth -= 1
                if self._depth == 0:
                    self._hiding = None
        elif tag in ("script", "style") and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if self._hiding is not None:
            if data.strip() and not self._counted:
                self.hidden += 1
                self._counted = True
        elif not self._skipping:
            self.parts.append(data)


_HIDING_STYLE: Final = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.I
)


def _hides(style: str | None) -> bool:
    return bool(style and _HIDING_STYLE.search(style))
