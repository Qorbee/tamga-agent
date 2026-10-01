import base64
import hashlib
import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text as sql

from app.modules.documents import service, unpacking
from app.platform import isolated, storage
from app.platform.db import open_session
from app.platform.isolated import Json, Outcome
from app.tests.conftest import guest_client
from app.tests.documents import archives
from app.tests.documents.pdfs import pdf, text
from app.tests.documents.test_file_content import PNG, upload

pytestmark = pytest.mark.integration

INVOICE = pdf(text("Invoice no INV-2026-118", y=700))


async def document(client: httpx.AsyncClient, document_id: str) -> dict[str, object]:
    detail: dict[str, object] = (await client.get(f"/documents/{document_id}")).json()
    return detail


async def parts(client: httpx.AsyncClient, document_id: str) -> list[dict[str, object]]:
    listed: list[dict[str, object]] = (await client.get(f"/documents/{document_id}/parts")).json()
    return listed


async def test_an_archive_opens_as_its_files_each_read_and_one_level_deep(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    deepest = archives.zip_of({"deepest.pdf": INVOICE})
    inner = archives.zip_of({"inner.pdf": INVOICE, "deeper.zip": deepest})
    outer = archives.zip_of(
        {"Fatura/invoice.pdf": INVOICE, "photo.png": PNG, "inner.zip": inner, "Thumbs.db": b"x"}
    )
    archive = await upload(client, "belgeler.zip", outer, "application/octet-stream")
    assert (await document(client, archive))["preview"] == "archive"
    assert await parts(client, archive) == []

    # The archive, then its files (the inner zip among them), then the inner zip's.
    while await service.read_pending():
        pass

    read = await document(client, archive)
    assert (read["read_state"], read["truncated"]) == ("read", False)
    listed = await parts(client, archive)
    assert [(p["position"], p["name"]) for p in listed] == [
        (0, "Fatura/invoice.pdf"),
        (1, "photo.png"),
        (2, "inner.zip"),
    ]
    invoice, photo, nested = (p["document"] for p in listed)
    assert isinstance(invoice, dict) and isinstance(photo, dict) and isinstance(nested, dict)
    # The recent files hold the archive, not the files taken out of it, which open from it.
    recent = (await client.get("/documents", params={"members": False})).json()
    assert [row["id"] for row in recent["items"]] == [archive]
    # Every file, though: the archive, the invoice, the photo, and the two archives inside it.
    assert (await client.get("/documents")).json()["total"] == 5
    # A file keeps the last part of its path as its own name, and says what holds it.
    assert (invoice["filename"], invoice["preview"], invoice["read_state"]) == (
        "invoice.pdf",
        "pdf",
        "read",
    )
    assert photo["preview"] == "image"
    assert (await document(client, str(invoice["id"])))["held_by"] == ["archive"]
    # Its name in the archive is the sender's: it keeps it.
    renamed = await client.patch(f"/documents/{invoice['id']}", json={"filename": "August.pdf"})
    assert renamed.status_code == 409
    # An archive inside the archive is opened; one inside that is only listed, never opened.
    assert nested["read_state"] == "read"
    inner_parts = await parts(client, str(nested["id"]))
    assert [p["name"] for p in inner_parts] == ["inner.pdf", "deeper.zip"]
    deeper = inner_parts[1]["document"]
    assert isinstance(deeper, dict) and deeper["read_state"] is None
    # The same PDF in both archives, and three times over, is one document.
    assert inner_parts[0]["document"] == {**invoice, "filename": "invoice.pdf"}


async def test_a_member_locked_by_a_password_is_listed_with_its_reason(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    archive = await upload(
        client, "locked.zip", archives.encrypted_zip("secret.pdf", INVOICE), "application/zip"
    )

    await service.read_pending()

    [locked] = await parts(client, archive)
    assert (locked["name"], locked["document"], locked["unreadable_reason"]) == (
        "secret.pdf",
        None,
        "password",
    )


async def test_an_archive_that_cannot_be_opened_leaves_only_its_reason(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    bomb = await upload(
        client, "bomb.zip", archives.zip_of({"a.bin": b"\0" * (5 * 1024 * 1024)}), "application/zip"
    )
    fake = await upload(client, "fake.zip", INVOICE, "application/zip")

    await service.read_pending()

    assert (await document(client, bomb))["unreadable_reason"] == "too_large"
    assert (await document(client, fake))["unreadable_reason"] == "damaged"
    assert await parts(client, bomb) == []
    assert (await client.get("/documents")).json()["total"] == 2


async def test_deleting_an_archive_takes_the_files_only_it_held(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    also_uploaded = pdf(text("Delivery note 77", y=700))
    archive = await upload(
        client,
        "files.rar",
        archives.rar5_of({"invoice.pdf": INVOICE, "note.pdf": also_uploaded}),
        "application/vnd.rar",
    )
    kept = await upload(client, "note.pdf", also_uploaded, "application/pdf")
    await service.read_pending()
    member = (await parts(client, archive))[0]["document"]
    assert isinstance(member, dict)

    # A file an archive holds is not deleted on its own.
    assert (await client.delete(f"/documents/{member['id']}")).status_code == 409
    assert (await client.delete(f"/documents/{archive}")).status_code == 204

    assert (await client.get(f"/documents/{member['id']}")).status_code == 404
    # One someone uploaded too stays, and is no longer held.
    assert (await document(client, kept))["held_by"] == []
    assert (await client.get("/documents")).json()["total"] == 1


async def test_a_winmail_dat_opens_as_the_files_outlook_wrapped(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    winmail = await upload(
        client,
        "winmail.dat",
        archives.winmail_of({"CMR 34 ABC 12.pdf": INVOICE, "foto.png": PNG}),
        "application/ms-tnef",
    )

    await service.read_pending()

    assert [p["name"] for p in await parts(client, winmail)] == ["CMR 34 ABC 12.pdf", "foto.png"]


async def test_a_picture_wrapped_under_a_name_that_says_nothing_is_still_drawn(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    winmail = await upload(
        client,
        "winmail.dat",
        archives.winmail_of({"image001": PNG, "notes": b"plain words"}),
        "application/ms-tnef",
    )

    await service.read_pending()

    picture, notes = (p["document"] for p in await parts(client, winmail))
    assert isinstance(picture, dict) and isinstance(notes, dict)
    assert (picture["content_type"], picture["preview"]) == ("image/png", "image")
    shown = await client.get(f"/documents/{picture['id']}/content")
    assert shown.headers["content-type"] == "image/png"
    # Bytes that prove nothing stay a download.
    assert (notes["content_type"], notes["preview"]) == ("application/octet-stream", None)


def replying(*replies: Json) -> Callable[..., Awaitable[Outcome]]:
    """A child that sends these replies, one per run, as a child a crafted file took over could."""
    queue = list(replies)

    async def run(name: str, data: bytes, hint: str, **_: object) -> Outcome:
        return Outcome(value=queue.pop(0))

    return run


def member(name: str, content: bytes) -> Json:
    return {"name": name, "size": len(content), "data": base64.b64encode(content).decode()}


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        # More files than the cap.
        (
            [
                {
                    "members": [member(f"{n}.pdf", b"%PDF") for n in range(3)],
                    "next": None,
                    "spent": 0,
                    "omitted": 0,
                }
            ],
            "damaged",
        ),
        # Runs that never end: one that sends nothing but says there is more.
        ([{"members": [], "next": 5, "spent": 0, "omitted": 0}], "damaged"),
        # More bytes than the total, whatever the child says it spent.
        (
            [
                {"members": [member("a.bin", b"x" * 40)], "next": 1, "spent": 0, "omitted": 0},
                {"members": [member("b.bin", b"y" * 40)], "next": None, "spent": 0, "omitted": 0},
            ],
            "too_large",
        ),
        # A size no row holds, for a file it did not take out.
        (
            [
                {
                    "members": [
                        {"name": "s.pdf", "size": 2**40, "data": None, "refused": "password"}
                    ],
                    "next": None,
                    "spent": 0,
                    "omitted": 0,
                }
            ],
            "damaged",
        ),
    ],
)
async def test_what_the_child_reports_is_held_to_the_limits_by_the_parent(
    client: httpx.AsyncClient,
    storage_bucket: None,
    monkeypatch: pytest.MonkeyPatch,
    replies: list[Json],
    reason: str,
) -> None:
    await guest_client(client)
    archive = await upload(client, "a.zip", archives.zip_of({"a.pdf": INVOICE}), "application/zip")
    monkeypatch.setattr(unpacking, "MAX_MEMBERS", 2)
    monkeypatch.setattr(unpacking, "MAX_TOTAL", 64)
    monkeypatch.setattr(isolated, "run", replying(*replies))

    await service.read_pending()

    assert (await document(client, archive))["unreadable_reason"] == reason
    assert await parts(client, archive) == []


async def test_an_archive_refused_partway_leaves_no_file_in_storage(
    client: httpx.AsyncClient, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    first, second, third = b"%PDF-1 " + b"a" * 30, b"%PDF-2 " + b"b" * 30, b"%PDF-3 " + b"c" * 30
    kept = await upload(client, "kept.pdf", second, "application/pdf")
    archive = await upload(
        client,
        "files.zip",
        archives.zip_of({"1.pdf": first, "2.pdf": second, "3.pdf": third}),
        "application/zip",
    )
    # The parent's own count passes the total at the third file, after two are stored. (The
    # child, forked from a server started before this test, keeps the real limits.)
    monkeypatch.setattr(unpacking, "MAX_TOTAL", 100)

    await service.read_pending()

    assert (await document(client, archive))["unreadable_reason"] == "too_large"
    async with open_session() as db:
        jobs = (
            await db.execute(
                sql("SELECT args FROM procrastinate_jobs WHERE task_name = 'documents.forget_file'")
            )
        ).scalars()
        for args in jobs:
            await service.forget_file(
                uuid.UUID(args["company_id"]), bytes.fromhex(args["sha256"]), args["storage_key"]
            )
    with pytest.raises(ClientError):
        await storage.get(f"{company_id}/{hashlib.sha256(first).hexdigest()}")
    # One stored before stays.
    assert await storage.get(f"{company_id}/{hashlib.sha256(second).hexdigest()}") == second
    assert (await document(client, kept))["id"] == kept


async def test_a_file_first_stored_under_a_name_that_says_nothing_learns_its_kind(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    inner = archives.zip_of({"note.pdf": INVOICE})
    bare = await upload(client, "ATT00001.bin", INVOICE, "application/octet-stream")
    bare_zip = await upload(client, "ATT00002.bin", inner, "application/octet-stream")
    await service.read_pending()
    # A PDF is read by its bytes, but not shown; nothing opens an archive it cannot name.
    assert (await document(client, bare))["preview"] is None
    assert (await document(client, bare_zip))["unreadable_reason"] == "unsupported"

    await upload(
        client,
        "files.zip",
        archives.zip_of({"invoice.pdf": INVOICE, "inner.zip": inner}),
        "application/zip",
    )
    while await service.read_pending():
        pass

    learned = await document(client, bare)
    assert (learned["filename"], learned["preview"], learned["read_state"]) == (
        "ATT00001.bin",
        "pdf",
        "read",
    )
    opened = await document(client, bare_zip)
    assert (opened["preview"], opened["read_state"]) == ("archive", "read")
    assert [p["name"] for p in await parts(client, bare_zip)] == ["note.pdf"]
