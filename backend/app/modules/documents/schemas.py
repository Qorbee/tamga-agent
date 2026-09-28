import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size: int
    doc_type: str | None
    sha256: str
    created_at: datetime


class DocumentDetail(DocumentOut):
    # A short-lived link that downloads the file under its name.
    download_url: str


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class DocumentUpdate(BaseModel):
    # A key from GET /document-types, or null when it is unknown.
    doc_type: str | None = Field(max_length=40)


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
