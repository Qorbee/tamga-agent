import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.modules.documents.models import ReadState, TypeSource
from app.modules.documents.sheets import Border, CellKind, HAlign, VAlign
from app.platform.filetypes import Preview
from app.platform.isolated import Reason


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size: int
    doc_type: str | None
    # Who gave the type: the owner, code reading the file, or a model; null while it has none.
    doc_type_source: TypeSource | None
    sha256: str
    created_at: datetime
    # How the web app can show it (GET /documents/{id}/content, /sheets, /parts, /rendition, /html
    # or /text); null: download only.
    preview: Preview | None
    # Whether Tamga has read what it says; null when nobody asked (an inline logo).
    read_state: ReadState | None
    # Why it could not be read, when read_state is unreadable.
    unreadable_reason: Reason | None
    # Read only in part: pages, sheets, rows or columns past what a file is read for.
    truncated: bool
    # An office file's PDF is made (GET /documents/{id}/rendition), shown in its place.
    converted: bool


class Holder(StrEnum):
    """What keeps a file besides the uploads of it: a mail it came in, a project it is evidence
    on."""

    MAIL = "mail"
    PROJECT = "project"
    # Taken out of an archive or a winmail.dat.
    ARCHIVE = "archive"


class DocumentDetail(DocumentOut):
    # A short-lived link that downloads the file under its name.
    download_url: str
    # What keeps it besides its uploads. Only a file nothing here holds can be deleted: one a mail
    # brought stays as long as the mail does, under the name it came with, and a project's evidence
    # stays with the project.
    held_by: list[Holder]


class DocumentPartOut(BaseModel):
    """A file in an archive, under the name it had there."""

    position: int
    # Its path in the archive: text to show.
    name: str
    size: int
    # Null when it could not be taken out; `unreadable_reason` says why.
    document: DocumentOut | None
    unreadable_reason: Reason | None


class HtmlOut(BaseModel):
    """A web page made safe to show, for a frame that runs no script."""

    html: str
    # Pictures on the web it points at, loaded only when the reader asks.
    remote_images: int


class TextOut(BaseModel):
    text: str
    # The file goes on past a million characters.
    truncated: bool


class CellStyleOut(BaseModel):
    bold: bool
    italic: bool
    underline: bool
    strike: bool
    # The font's name, for the reader's computer to draw with if it has it; null is the workbook's.
    font: str | None
    # Points; null is the workbook's `font_size`.
    size: float | None
    # `#rrggbb`, from the file; null is the default: black text, no fill.
    color: str | None
    fill: str | None
    # Null is Excel's General: numbers to the right, text to the left.
    align: HAlign | None
    # Null is the bottom, Excel's default.
    valign: VAlign | None
    wrap: bool
    indent: int
    top: Border | None
    right: Border | None
    bottom: Border | None
    left: Border | None


class CellOut(BaseModel):
    kind: CellKind
    # A number as a number; a date as ISO text without a zone; text, TRUE/FALSE or an error code
    # as text; "" for a blank cell that has only a style.
    value: str | float
    # Excel's number format code (`General`, `#,##0.00`, `0%`, `dd.mm.yyyy`), for showing the value
    # as Excel would in the reader's language.
    format: str
    # Index into the sheet's `styles`; 0 is the default.
    style: int


class RowOut(BaseModel):
    # Null where a cell is empty; not padded to one length.
    cells: list[CellOut | None]
    # Points; null is the sheet's default height.
    height: float | None
    hidden: bool


class ColumnOut(BaseModel):
    # Pixels at 100%, as Excel draws it; null is the sheet's default width.
    width: int | None
    hidden: bool


class MergeOut(BaseModel):
    # Zero-based, from the top-left cell.
    row: int
    column: int
    rows: int
    columns: int


class SheetOut(BaseModel):
    name: str
    columns: list[ColumnOut]
    rows: list[RowOut]
    merges: list[MergeOut]
    styles: list[CellStyleOut]
    # The sheet has more rows or columns than are shown.
    truncated: bool
    # A column's width in pixels and a row's height in points, where the sheet sets none.
    default_width: int
    default_height: float
    # Hidden by its workbook: Excel shows no tab for it.
    hidden: bool


class WorkbookOut(BaseModel):
    sheets: list[SheetOut]
    # Sheets past the cap, not shown.
    omitted: int
    # The font and size (points) of every cell whose style names none, Excel's Normal style's;
    # a null font leaves it to the app.
    font: str | None
    font_size: float


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class DocumentUpdate(BaseModel):
    """Only the fields sent change."""

    # A key from GET /document-types, or null when it is unknown.
    doc_type: str | None = Field(default=None, max_length=40)
    # The name Tamga shows it under. It keeps the file's extension, which says what kind of file it
    # is. A file a mail or an archive brought keeps the name it came with: the one name for it
    # everywhere, and the sender's.
    filename: str | None = Field(default=None, min_length=1, max_length=300)


class UploadOut(BaseModel):
    id: uuid.UUID
    document: DocumentOut
    filename: str
    note: str
    # False when the company already had this file: the upload points at the stored document.
    new_document: bool
    created_at: datetime


class DocumentTypeOut(BaseModel):
    key: str
    label: str
    pack: str
    proof_of_delivery: bool
    # The fields read from a file of this type, as JSON Schema.
    fields: dict[str, Any]
