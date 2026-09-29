"""A spreadsheet read into rows of text, capped: what the canvas shows of an Excel file.

Cells come back as the text a person would read (a whole number without ``.0``, a date without a
midnight time), so the web app never guesses at a cell's type. The file is from outside and
parsing it is synchronous: callers run ``read_sheets`` in a worker thread, and anything that goes
wrong inside a file is ``Unreadable``, never a crash.
"""

import io
import logging
import warnings
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final

import openpyxl
import xlrd

from app.platform.filetypes import XLS, XLSX

logger = logging.getLogger(__name__)
# openpyxl warns about every styling feature it skips (a header, a data validation); none of them
# changes a cell's value, and a warning per file would bury the log.
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

MAX_SHEETS: Final = 20
MAX_ROWS: Final = 1000
MAX_COLUMNS: Final = 50
# An .xlsx is zipped XML: one that inflates past this is refused before it is parsed (a zip bomb).
_MAX_UNZIPPED: Final = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Sheet:
    name: str
    rows: list[list[str | None]]
    # The sheet has more rows or columns than were read.
    truncated: bool


class Unreadable(ValueError):
    pass


def read_sheets(data: bytes, media: str) -> list[Sheet]:
    try:
        if media == XLSX:
            return _xlsx(data)
        if media == XLS:
            return _xls(data)
    except Unreadable:
        raise
    except Exception as error:  # a broken or hostile file fails in many ways; all mean "unreadable"
        logger.info("a spreadsheet could not be read: %s", type(error).__name__)
        raise Unreadable("The spreadsheet cannot be read") from error
    raise Unreadable("Not a spreadsheet")


def _xlsx(data: bytes) -> list[Sheet]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > _MAX_UNZIPPED:
            raise Unreadable("The spreadsheet is too large to show")
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = []
        for worksheet in workbook.worksheets[:MAX_SHEETS]:
            rows: list[list[str | None]] = []
            truncated = False
            for row in worksheet.iter_rows(values_only=True):
                if len(rows) == MAX_ROWS:
                    truncated = True
                    break
                # Rows are padded to the width the sheet declares, which can be Excel's full 16,384
                # columns: only a value past the limit is lost, and counting runs in C.
                past = row[MAX_COLUMNS:]
                truncated = truncated or past.count(None) < len(past)
                rows.append([_text(value) for value in row[:MAX_COLUMNS]])
            sheets.append(Sheet(name=worksheet.title, rows=_trim(rows), truncated=truncated))
        return sheets
    finally:
        workbook.close()


def _xls(data: bytes) -> list[Sheet]:
    book = xlrd.open_workbook(file_contents=data, on_demand=True)
    try:
        sheets = []
        for index in range(min(book.nsheets, MAX_SHEETS)):
            sheet = book.sheet_by_index(index)
            rows = [
                [_xls_text(cell, book.datemode) for cell in sheet.row(number)[:MAX_COLUMNS]]
                for number in range(min(sheet.nrows, MAX_ROWS))
            ]
            truncated = sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS
            sheets.append(Sheet(name=sheet.name, rows=_trim(rows), truncated=truncated))
            book.unload_sheet(index)
        return sheets
    finally:
        book.release_resources()


def _xls_text(cell: xlrd.sheet.Cell, datemode: int) -> str | None:
    if cell.ctype == xlrd.XL_CELL_DATE:
        return _text(xlrd.xldate.xldate_as_datetime(cell.value, datemode))
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return _text(bool(cell.value))
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return str(xlrd.error_text_from_code.get(cell.value, "#ERROR"))
    return _text(cell.value)


def _text(value: object) -> str | None:
    match value:
        case None:
            return None
        case bool():
            return "TRUE" if value else "FALSE"
        case int():
            return str(value)
        case float():
            # Excel keeps every number as a float: 12 is stored as 12.0, and sums carry noise.
            return str(int(value)) if value.is_integer() else format(value, ".15g")
        case datetime():
            if value.time() == time():
                return value.date().isoformat()
            return value.isoformat(sep=" ", timespec="minutes")
        case date():
            return value.isoformat()
        case time():
            return value.isoformat(timespec="minutes")
        case timedelta():
            return str(value)
        case _:
            text = str(value).replace("\x00", "")
            return text or None


def _trim(rows: list[list[str | None]]) -> list[list[str | None]]:
    """Drops empty cells at the end of each row and empty rows at the end of the sheet."""
    trimmed = []
    for row in rows:
        end = len(row)
        while end and row[end - 1] is None:
            end -= 1
        trimmed.append(row[:end])
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed
