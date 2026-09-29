import io
import time
import zipfile
from collections.abc import Callable
from datetime import datetime

import openpyxl
import pytest
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.colors import Color

from app.modules.documents import sheets
from app.platform.filetypes import XLS, XLSX


def saved(book: openpyxl.Workbook) -> bytes:
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def workbook(*rows: list[object], extra_sheet: bool = False) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Seferler"
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        book.create_sheet("Boş")
    return saved(book)


def rewritten(
    data: bytes, transform: Callable[[bytes], bytes], path: str = "xl/worksheets/sheet1.xml"
) -> bytes:
    """The workbook with one part's XML changed, for what openpyxl will not write."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            body = source.read(entry)
            if entry.filename == path:
                body = transform(body)
            target.writestr(entry, body)
    return out.getvalue()


def values(sheet: sheets.Sheet) -> list[list[object]]:
    return [[cell.value if cell else None for cell in row.cells] for row in sheet.rows]


def test_each_cell_keeps_its_value_and_number_format() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Plaka", "Tutar", "Pay", "Tarih", "Ödendi"])
    sheet.append(["34 ABC 12", 1234.5, 0.25, datetime(2026, 9, 28, 14, 30), True])
    sheet["B2"].number_format = "#,##0.00"
    sheet["C2"].number_format = "0%"
    sheet["D2"].number_format = "dd.mm.yyyy hh:mm"
    sheet["A3"] = "=1/0"  # a formula whose cached value Excel would write; openpyxl writes none

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    plate, amount, share, when, paid = read.rows[1].cells
    assert plate == sheets.Cell(kind="text", value="34 ABC 12", format="General", style=0)
    assert amount is not None and (amount.kind, amount.value, amount.format) == (
        "number",
        1234.5,
        "#,##0.00",
    )
    assert share is not None and (share.value, share.format) == (0.25, "0%")
    assert when is not None and (when.kind, when.value) == ("date", "2026-09-28T14:30:00")
    assert paid is not None and (paid.kind, paid.value) == ("bool", "TRUE")
    # No cached value: nothing to show, and the empty row is trimmed.
    assert len(read.rows) == 2


def test_the_layout_comes_with_the_cells() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "FATURA"
    sheet["A1"].font = Font(name="Arial", bold=True, size=16, color="FF1F4E79")
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    sheet.merge_cells("A1:D1")
    sheet["B3"] = "Toplam"
    sheet["B3"].fill = PatternFill("solid", fgColor=Color(theme=4, tint=0.8))
    sheet["B3"].border = Border(top=Side(style="thin"), bottom=Side(style="double"))
    sheet["C4"].border = Border(left=Side(style="medium"))  # styled, with no value
    sheet.column_dimensions["B"].width = 30
    sheet.column_dimensions["D"].hidden = True
    sheet.row_dimensions[1].height = 28
    sheet.row_dimensions[2].hidden = True

    read_book = sheets.read_workbook(saved(book), XLSX)
    [read] = read_book.sheets

    assert read.merges == [sheets.Merge(row=0, column=0, rows=1, columns=4)]
    # A width of 30 as Excel stores it, padding included, is 210 pixels; unset ones are the
    # sheet's default of 64.
    assert read.columns[1].width == 210 and read.columns[3].hidden
    assert read.columns[0].width is None and read.default_width == 64
    assert read.rows[0].height == 28 and read.rows[1].hidden and read.default_height == 15
    title = read.rows[0].cells[0]
    assert title is not None
    style = read.styles[title.style]
    assert (read_book.font, read_book.font_size) == ("Calibri", 11) and style.font == "Arial"
    assert style.bold and style.size == 16 and style.color == "#1f4e79"
    assert (style.align, style.valign) == ("center", "middle")
    total = read.rows[2].cells[1]
    assert total is not None
    boxed = read.styles[total.style]
    # Accent 1 of openpyxl's theme (Office 2007's #4f81bd) at Excel's "Lighter 80%".
    assert boxed.fill == "#dce6f2"
    assert (boxed.top, boxed.bottom) == ("thin", "double")
    blank = read.rows[3].cells[2]
    assert blank is not None and blank.kind == "blank"
    assert read.styles[blank.style].left == "medium"
    assert read.styles[0] == sheets.Style()


def test_a_merge_keeps_the_empty_rows_it_reaches_down_into() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "FATURA"
    sheet.merge_cells("A1:A4")

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    assert len(read.rows) == 4 and read.merges == [sheets.Merge(row=0, column=0, rows=4, columns=1)]


def test_the_sheets_own_default_sizes_are_used() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "x"
    sheet.sheet_format.defaultRowHeight = 22
    sheet.sheet_format.baseColWidth = 10

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    # 10 digits of 7 pixels and 5 of padding, rounded up to 8 pixels as Excel does.
    assert (read.default_width, read.default_height) == (80, 22)


def test_merges_past_the_rows_shown_are_found_without_parsing_those_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sheets, "MAX_ROWS", 3)
    # Chunks this small cut every tag in two somewhere, the merge list's included.
    monkeypatch.setattr(sheets, "_CHUNK", 16)
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for number in range(1, 40):
        sheet.cell(row=number, column=1, value=f"satır {number}")
    sheet.merge_cells("B1:C2")
    sheet.merge_cells("A30:B31")  # below the rows shown: clipped away

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    assert read.truncated and len(read.rows) == 3
    assert read.merges == [sheets.Merge(row=0, column=1, rows=2, columns=2)]


def test_a_malformed_merge_or_font_name_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    # openpyxl refuses the whole file for a malformed merge it reaches; past the rows shown, only
    # the byte search reads the merges.
    monkeypatch.setattr(sheets, "MAX_ROWS", 1)
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["x"])
    sheet.append(["y"])
    sheet["A1"].font = Font(name='Arial"; background: red')
    sheet.merge_cells("A1:B1")
    sheet.merge_cells("A3:B3")
    data = rewritten(saved(book), lambda xml: xml.replace(b'ref="A3:B3"', b'ref="A3:&#1234;"'))

    [read] = sheets.read_workbook(data, XLSX).sheets

    assert read.merges == [sheets.Merge(row=0, column=0, rows=1, columns=2)]
    cell = read.rows[0].cells[0]
    assert cell is not None and read.styles[cell.style].font is None


def test_the_normal_styles_font_is_the_default_a_style_is_told_apart_from() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "plain"
    sheet["A2"] = "bigger"
    sheet["A2"].font = Font(name="Arial", size=11)
    data = rewritten(
        saved(book),
        lambda xml: xml.replace(b'<name val="Calibri" />', b'<name val="Arial" />', 1).replace(
            b'<sz val="11" />', b'<sz val="10" />', 1
        ),
        path="xl/styles.xml",
    )

    read = sheets.read_workbook(data, XLSX)

    assert (read.font, read.font_size) == ("Arial", 10)
    [sheet_read] = read.sheets
    bigger = sheet_read.rows[1].cells[0]
    assert bigger is not None
    assert (sheet_read.styles[bigger.style].font, sheet_read.styles[bigger.style].size) == (
        None,
        11,
    )


def test_a_hostile_column_range_is_refused_without_walking_it() -> None:
    # The layout pass runs before openpyxl, which refuses the column once it streams the rows; an
    # unclamped range would first walk a billion columns.
    data = rewritten(
        workbook(["x"]),
        lambda xml: xml.replace(
            b"<sheetData>",
            b'<cols><col min="-1000000000" max="1" width="20" customWidth="1" /></cols><sheetData>',
        ),
    )

    started = time.perf_counter()
    with pytest.raises(sheets.Unreadable):
        sheets.read_workbook(data, XLSX)
    assert time.perf_counter() - started < 1


def test_a_merge_past_the_last_column_marks_the_sheet_cut() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "Başlık"
    sheet.merge_cells("A1:BZ1")

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    assert read.truncated and read.merges == [sheets.Merge(row=0, column=0, rows=1, columns=50)]


def test_a_large_sheet_is_cut_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sheets, "MAX_ROWS", 2)
    monkeypatch.setattr(sheets, "MAX_COLUMNS", 2)

    [sheet] = sheets.read_workbook(workbook(["a", "b", "c"], ["d"], ["e"]), XLSX).sheets

    assert sheet.truncated and values(sheet) == [["a", "b"], ["d"]]


def test_a_value_far_past_the_last_column_still_marks_the_sheet_cut() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "Plaka"
    sheet.cell(row=1, column=60, value="note")

    [read] = sheets.read_workbook(saved(book), XLSX).sheets

    assert read.truncated and values(read) == [["Plaka"]]


def test_sheets_past_the_cap_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sheets, "MAX_SHEETS", 1)

    read = sheets.read_workbook(workbook(["a"], extra_sheet=True), XLSX)

    assert [sheet.name for sheet in read.sheets] == ["Seferler"] and read.omitted == 1


def test_a_broken_file_or_a_zip_bomb_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(sheets.Unreadable):
        sheets.read_workbook(b"PK\x03\x04 not really a zip", XLSX)
    with pytest.raises(sheets.Unreadable):
        sheets.read_workbook(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 not really a workbook", XLS)

    monkeypatch.setattr(sheets, "_MAX_UNZIPPED", 100)
    with pytest.raises(sheets.Unreadable, match="too large"):
        sheets.read_workbook(workbook(["x" * 500]), XLSX)
