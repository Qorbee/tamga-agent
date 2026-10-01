import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response, status

from app.modules.documents import service
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    DocumentPartOut,
    DocumentTypeOut,
    DocumentUpdate,
    HtmlOut,
    TextOut,
    UploadOut,
    WorkbookOut,
)
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

documents = APIRouter(tags=["documents"])

# The body is the file itself, typed by its Content-Type: no multipart parser, and the size limit
# is checked while it streams in.
_RAW_FILE = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@documents.post("/documents", status_code=status.HTTP_201_CREATED, openapi_extra=_RAW_FILE)
async def upload_document(
    request: Request,
    actor: ActorDep,
    db: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=300)],
    doc_type: Annotated[str | None, Query(max_length=40)] = None,
    note: Annotated[str, Query(max_length=1000)] = "",
) -> UploadOut:
    return await service.upload(
        db,
        actor,
        chunks=request.stream(),
        filename=filename,
        content_type=request.headers.get("content-type", "")[:200],
        doc_type=doc_type,
        note=note,
    )


@documents.get("/documents")
async def list_documents(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    doc_type: Annotated[str | None, Query(max_length=40)] = None,
    # Only files nobody has typed yet: the upload page's "to sort" list.
    untyped: bool = False,
    # False leaves out the files taken out of an archive or a winmail.dat, which open from it,
    # unless someone uploaded them too: the upload page's recent files.
    members: bool = True,
) -> DocumentPage:
    """Newest first, by when each file arrived or was last uploaded."""
    return await service.list_documents(
        db, actor, q=q, doc_type=doc_type, untyped=untyped, members=members, page=page
    )


@documents.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> DocumentDetail:
    return await service.get_document(db, actor, document_id)


@documents.get(
    "/documents/{document_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The file"}},
)
async def get_document_content(document_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> Response:
    """The file itself, for the web app to draw: inline only as a verified image or PDF."""
    return _file(await service.get_content(db, actor, document_id))


@documents.get(
    "/documents/{document_id}/rendition",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The PDF"}},
)
async def get_document_rendition(
    document_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> Response:
    """The PDF the converter made of a Word, RTF or OpenDocument file (`converted`)."""
    return _file(await service.get_rendition(db, actor, document_id))


@documents.get("/documents/{document_id}/html")
async def get_document_html(document_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> HtmlOut:
    """A web page (`preview` html) made safe to show, as a mail's body is: for a frame that runs
    no script and loads nothing from the web unless asked."""
    return await service.get_html(db, actor, document_id)


@documents.get("/documents/{document_id}/text")
async def get_document_text(document_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> TextOut:
    """An XML or text file (`preview` text) as text, up to a million characters."""
    return await service.get_text(db, actor, document_id)


def _file(file: service.FileContent) -> Response:
    disposition = "inline" if file.inline else "attachment"
    name = quote(file.filename, safe="")
    return Response(
        content=file.data,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{name}",
            "X-Content-Type-Options": "nosniff",
            # Opened in a tab, a file from outside still never runs as a page on the API's origin.
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # A company's file never stays in a shared browser for the next person who signs in.
            "Cache-Control": "no-store",
        },
    )


@documents.get("/documents/{document_id}/parts")
async def list_document_parts(
    document_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> list[DocumentPartOut]:
    """The files taken out of an archive or a winmail.dat, in their order there, each under the
    name it had in it. Empty until the archive is read (`read_state`)."""
    return await service.get_parts(db, actor, document_id)


@documents.get("/documents/{document_id}/sheets")
async def get_document_sheets(
    document_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> WorkbookOut:
    """A spreadsheet as Excel lays it out: each cell's value and number format, the styles, merged
    cells, widths and heights. At most 20 sheets of a thousand rows and fifty columns."""
    return await service.get_sheets(db, actor, document_id)


@documents.patch("/documents/{document_id}")
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, actor: ActorDep, db: SessionDep
) -> DocumentOut:
    """Its type or the name Tamga shows it under; only the fields sent change. A new name keeps the
    extension (400), and a file a mail or an archive brought keeps the name it came with (409,
    `held_by`)."""
    return await service.update_document(db, actor, document_id, body)


@documents.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    """An uploaded file, with what was read from it. 409 when a mail brought it or a project holds
    it as evidence (`held_by`)."""
    await service.delete_document(db, actor, document_id)


@documents.get("/document-types")
async def list_document_types(actor: ActorDep) -> list[DocumentTypeOut]:
    return service.document_types()
