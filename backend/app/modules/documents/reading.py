"""What a file says, read by code in a locked child (ADR-0012), leaving out what its viewer hides
(ADR-0013).

Each reader runs only through ``isolated.run``, gets the bytes and returns JSON:
- a PDF's text layer, page by page, without text drawn invisible or under 1 pt; a page with no text
  is a scan, for the vision model;
- a PDF's pages as pictures, as a viewer paints them: what the vision model is shown of a scan;
- a spreadsheet's visible cells, row by row, without hidden sheets, rows and columns;
- the spreadsheet as Excel lays it out, for the canvas;
- a web page's text as the canvas shows it, XML's values, a text file as it is.

What is left out is counted, so a file full of hidden text is itself a signal.
"""

import base64
import ctypes
import dataclasses
import math
import re
import struct
import zlib
from html import unescape
from typing import Final

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pydantic import BaseModel, Field

from app.config import VISION_PAGES_PER_FILE
from app.modules.documents import sheets
from app.platform import filetypes, html
from app.platform.isolated import Json, Reason, Refused, register


class PdfText(BaseModel):
    """What ``pdf_text`` sends back, as the parent checks it."""

    pages: list[str]
    page_count: int = Field(ge=0)
    hidden: int = Field(ge=0)


# Pages of one PDF drawn as pictures in one run: what one call shows a model (ADR-0013).
MAX_PICTURED: Final = VISION_PAGES_PER_FILE


class PagesHint(BaseModel):
    """What ``page_images`` is told: which pages to draw, counted from zero."""

    pages: list[int] = Field(max_length=MAX_PICTURED)


class PageImages(BaseModel):
    """What ``page_images`` sends back, as the parent checks it: a PNG per page asked for, in
    base64."""

    images: list[str] = Field(max_length=MAX_PICTURED)


class SheetText(BaseModel):
    """What ``sheet_text`` sends back, as the parent checks it."""

    text: str
    sheet_count: int = Field(ge=0)
    hidden: int = Field(ge=0)
    # Sheets, rows or columns past what a workbook is read for (sheets.MAX_*) were not read.
    truncated: bool


class TextHint(BaseModel):
    """What ``file_text`` is told of the file: its type, and the charset it was declared in."""

    media: str
    charset: str | None = None


class FileText(BaseModel):
    """What ``file_text`` sends back, as the parent checks it."""

    text: str
    hidden: int = Field(ge=0)


# Pages a PDF is read for by code; the rest are counted, not read.
MAX_PAGES: Final = 100
# PDF text drawn in these modes is never painted: 3 draws nothing, 7 only clips.
_UNPAINTED: Final = {pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE, pdfium_c.FPDF_TEXTRENDERMODE_CLIP}
# What each painting mode paints: its fill, its outline (stroke), or both. A glyph shows when one
# of the parts its mode paints is not fully transparent.
_FILLED: Final = {
    pdfium_c.FPDF_TEXTRENDERMODE_FILL,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_CLIP,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE_CLIP,
}
_STROKED: Final = {
    pdfium_c.FPDF_TEXTRENDERMODE_STROKE,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE,
    pdfium_c.FPDF_TEXTRENDERMODE_STROKE_CLIP,
    pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE_CLIP,
}
# A page is drawn at 144 dots an inch, where small print on a form stays legible, and never as
# more pixels than this, give or take the row and column that rounding a side up adds (an A4 page
# is two million): twenty pages that do not compress at all then still fit, in base64, in the
# 64 MiB a child may send back.
_PICTURE_SCALE: Final = 2.0
_PICTURE_PIXELS: Final = 2_100_000
# XML: a CDATA section's text is a value as it stands; a comment is none; a tag or a declaration
# is what lies between the values.
_CDATA: Final = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_COMMENT: Final = re.compile(r"<!--.*?-->", re.S)
_TAG: Final = re.compile(r"<[^>]*>")
# Control characters a database text column refuses (NUL) or that carry nothing to read.
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _pdf(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        if error.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            raise Refused(Reason.PASSWORD) from error
        raise Refused(Reason.DAMAGED) from error


@register("documents.pdf_text")
def pdf_text(data: bytes, hint: str) -> Json:
    document = _pdf(data)
    try:
        count = len(document)
        pages: list[Json] = []
        hidden = 0
        for index in range(min(count, MAX_PAGES)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                text, left_out = _visible_text(textpage.raw)
            finally:
                textpage.close()
                page.close()
            pages.append(text)
            hidden += left_out
    finally:
        document.close()
    return {"pages": pages, "page_count": count, "hidden": hidden}


def _visible_text(textpage: pdfium_c.FPDF_TEXTPAGE) -> tuple[str, int]:
    """The page's characters a viewer paints, and how many it would not."""
    units: list[bytes] = []
    left_out = 0
    for index in range(pdfium_c.FPDFText_CountChars(textpage)):
        code = pdfium_c.FPDFText_GetUnicode(textpage, index)
        text_object = pdfium_c.FPDFText_GetTextObject(textpage, index)
        # Characters PDFium adds itself (a space between words, a line break) have no object.
        if text_object:
            mode = pdfium_c.FPDFTextObj_GetTextRenderMode(text_object)
            unseen = (
                mode in _UNPAINTED
                or pdfium_c.FPDFText_GetFontSize(textpage, index) < 1
                or not _painted(textpage, index, mode)
            )
            if unseen:
                left_out += not chr(code).isspace()
                continue
        # A code point, or half of a UTF-16 pair for a character past the first plane: joined as
        # UTF-16 and decoded once, so a pair becomes its character and a lone half a U+FFFD.
        units.append(chr(code).encode("utf-16-le", "surrogatepass"))
    text = b"".join(units).decode("utf-16-le", errors="replace")
    return plain(text), left_out


def plain(text: str) -> str:
    """Text a column keeps: one kind of line break, and no control character (a NUL the database
    refuses, a form feed that would read as a page's end)."""
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _painted(textpage: pdfium_c.FPDF_TEXTPAGE, index: int, mode: int) -> bool:
    """Whether a part the mode paints (fill, outline) is not fully transparent."""
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    colour = (ctypes.byref(red), ctypes.byref(green), ctypes.byref(blue), ctypes.byref(alpha))
    parts = (
        (_FILLED, pdfium_c.FPDFText_GetFillColor),
        (_STROKED, pdfium_c.FPDFText_GetStrokeColor),
    )
    return any(
        mode in modes and read(textpage, index, *colour) and alpha.value > 0
        for modes, read in parts
    )


@register("documents.page_images")
def page_images(data: bytes, hint: str) -> Json:
    """The pages asked for, each as a viewer paints it, as a grey PNG. Text drawn invisible is not
    painted, so what ``pdf_text`` left out is not in the picture either; what is typed into a form's
    fields is painted, though it is not in the text layer."""
    request = PagesHint.model_validate_json(hint)
    document = _pdf(data)
    try:
        # Before any page is opened: a form's fields are drawn only for pages opened after it.
        document.init_forms()
        images: list[Json] = []
        for index in request.pages:
            if not 0 <= index < len(document):
                raise Refused(Reason.DAMAGED)
            page = document[index]
            try:
                width, height = page.get_size()
                area = max(width * height, 1.0)
                bitmap = page.render(
                    scale=min(_PICTURE_SCALE, math.sqrt(_PICTURE_PIXELS / area)),
                    grayscale=True,
                )
            except (pdfium.PdfiumError, ValueError) as error:
                raise Refused(Reason.DAMAGED) from error
            finally:
                page.close()
            try:
                images.append(base64.b64encode(_png(bitmap)).decode("ascii"))
            finally:
                bitmap.close()
    finally:
        document.close()
    return {"images": images}


def _png(bitmap: pdfium.PdfBitmap) -> bytes:
    """A grey bitmap as a PNG: a byte a pixel, each row unfiltered, deflated. Written here because
    it is this short; an imaging library would be a dependency for one function."""
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    if bitmap.n_channels != 1:
        raise Refused(Reason.DAMAGED)
    pixels = bytes(bitmap.buffer)
    rows = b"".join(b"\x00" + pixels[row * stride : row * stride + width] for row in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


@register("documents.sheet_text")
def sheet_text(data: bytes, media: str) -> Json:
    book = _workbook(data, media)
    text, hidden = sheets.visible_text(book)
    truncated = book.omitted > 0 or any(sheet.truncated for sheet in book.sheets)
    return {
        "text": text,
        "sheet_count": len(book.sheets) + book.omitted,
        "hidden": hidden,
        "truncated": truncated,
    }


@register("documents.workbook")
def workbook(data: bytes, media: str) -> Json:
    """The spreadsheet as Excel lays it out, for the canvas; validated by the caller."""
    laid_out: Json = dataclasses.asdict(_workbook(data, media))
    return laid_out


def _workbook(data: bytes, media: str) -> sheets.Workbook:
    try:
        return sheets.read_workbook(data, media)
    except sheets.TooLarge as error:
        raise Refused(Reason.TOO_LARGE) from error
    except sheets.Unreadable as error:
        raise Refused(Reason.DAMAGED) from error


@register("documents.file_text")
def file_text(data: bytes, hint: str) -> Json:
    """A web page's text as the canvas shows it: after the sanitizer, without its styles and
    colours, so nothing read was hidden from the person. XML's values without its tags, a CDATA
    section's as they stand. A text file as it is."""
    request = TextHint.model_validate_json(hint)
    decoded = filetypes.decode_text(data, request.charset)
    if decoded is None:
        raise Refused(Reason.DAMAGED)
    if request.media == filetypes.HTML:
        shown = html.shown_text(html.sanitize(decoded, styled=False).html)
        return {"text": shown.text, "hidden": shown.hidden}
    if request.media == filetypes.XML:
        return {"text": _xml_values(decoded), "hidden": 0}
    return {"text": decoded, "hidden": 0}


def _xml_values(xml: str) -> str:
    pieces = [
        piece if index % 2 else unescape(_TAG.sub("\n", _COMMENT.sub("", piece)))
        for index, piece in enumerate(_CDATA.split(xml))
    ]
    lines = (" ".join(line.split()) for line in "".join(pieces).splitlines())
    return "\n".join(line for line in lines if line)
