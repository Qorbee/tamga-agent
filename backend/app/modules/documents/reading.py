"""What a file says, read by code in a locked child (ADR-0012), leaving out what its viewer hides
(ADR-0013).

Each reader runs only through ``isolated.run``, gets the bytes and returns JSON:
- a PDF's text layer, page by page, without text drawn invisible or under 1 pt; a page with no text
  is a scan, for the vision model;
- a spreadsheet's visible cells, row by row, without hidden sheets, rows and columns;
- the spreadsheet as Excel lays it out, for the canvas.

What is left out is counted, so a file full of hidden text is itself a signal.
"""

import ctypes
import dataclasses
import re
from typing import Final

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pydantic import BaseModel, Field

from app.modules.documents import sheets
from app.platform.isolated import Json, Reason, Refused, register


class PdfText(BaseModel):
    """What ``pdf_text`` sends back, as the parent checks it."""

    pages: list[str]
    page_count: int = Field(ge=0)
    hidden: int = Field(ge=0)


class SheetText(BaseModel):
    """What ``sheet_text`` sends back, as the parent checks it."""

    text: str
    sheet_count: int = Field(ge=0)
    hidden: int = Field(ge=0)
    # Sheets, rows or columns past what a workbook is read for (sheets.MAX_*) were not read.
    truncated: bool


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
# Control characters a database text column refuses (NUL) or that carry nothing to read.
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@register("documents.pdf_text")
def pdf_text(data: bytes, hint: str) -> Json:
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        if error.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            raise Refused(Reason.PASSWORD) from error
        raise Refused(Reason.DAMAGED) from error
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
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")), left_out


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
