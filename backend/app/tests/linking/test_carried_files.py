"""A file that came in a project's mail is in the project as its mail is (ADR-0021): suggested,
confirmed or rejected with it, moved and merged with it, and left alone once the owner answered
it."""

import email
import email.policy
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from app.config import get_settings
from app.modules.conversations.models import MessageAttachment, ThreadCategory
from app.modules.documents import service as documents
from app.modules.linking import service
from app.platform import llm
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.documents import pdfs
from app.tests.documents.archives import zip_of
from app.tests.documents.test_vision import sized_png
from app.tests.linking.test_placing import (
    NEW_PROJECT,
    Script,
    arrive,
    mail,
    model,  # noqa: F401  (the fixture)
    owner_project,
    the_projects,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

ORDER = "<order@x>"
CMR = {"CMR 4471.pdf": b"%PDF-1.7 the truck's consignment note"}


async def proposed(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    attach: dict[str, bytes] = CMR,
) -> dict[str, Any]:
    """A sandbox with one conversation, its mail carrying ``attach``, that the linker proposed a
    project for. Returns the placement as the API gives it."""
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail(ORDER, attach=attach), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [placement] = (await client.get("/placements")).json()["items"]
    return {"company_id": str(company_id), **placement}


async def files_of(client: httpx.AsyncClient, project_id: str | uuid.UUID) -> dict[str, Any]:
    """A project's file links by the file's name."""
    page = await client.get(
        "/project-links", params={"project_id": str(project_id), "item_kind": "document"}
    )
    links = page.json()["items"]
    ids = [uuid.UUID(link["item_id"]) for link in links]
    async with open_session() as db:
        rows = await db.execute(
            select(MessageAttachment.document_id, MessageAttachment.filename).where(
                MessageAttachment.document_id.in_(ids)
            )
        )
        names = {document_id: filename for document_id, filename in rows.tuples()}
    return {names[uuid.UUID(link["item_id"])]: link for link in links}


async def test_a_file_waits_with_its_mail_and_is_confirmed_with_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    project_id = placement["project_id"]

    [cmr] = (await files_of(client, project_id)).values()
    assert (cmr["method"], cmr["status"]) == ("mail", "suggested")
    # The owner answers the mail, not each file it carried: only the mail waits.
    project = (await client.get(f"/projects/{project_id}")).json()
    assert (project["suggested_links"], project["confirmed_links"]) == (1, 0)

    confirmed = await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})
    assert confirmed.status_code == 200, confirmed.text
    [cmr] = (await files_of(client, project_id)).values()
    assert (cmr["method"], cmr["status"]) == ("mail", "confirmed")
    assert cmr["decided_at"] is not None and cmr["decided_by"] is None


async def test_a_file_is_rejected_with_its_mail(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)

    rejected = await client.patch(f"/placements/{placement['id']}", json={"links": "rejected"})

    assert rejected.status_code == 200, rejected.text
    [cmr] = (await files_of(client, placement["project_id"])).values()
    assert cmr["status"] == "rejected"


async def test_a_file_moves_with_its_conversation(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    url, was = f"/placements/{placement['id']}", placement["project_id"]
    await arrive(company_id, 2, mail("<other@x>", subject="Another"), category=None)
    own = await owner_project(company_id, "<other@x>", name="The owner's own")
    assert (await client.patch(url, json={"links": "confirmed"})).status_code == 200

    moved = await client.patch(url, json={"project_id": str(own.id), "from_project_id": was})

    assert moved.status_code == 200, moved.text
    [left] = (await files_of(client, was)).values()
    [there] = (await files_of(client, own.id)).values()
    assert (left["status"], there["status"]) == ("rejected", "confirmed")
    assert left["item_id"] == there["item_id"]


async def test_a_file_the_owner_answered_is_left_alone(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    [cmr] = (await files_of(client, placement["project_id"])).values()

    # The owner says the file is not of this job, though its mail is.
    answered = await client.patch(f"/project-links/{cmr['id']}", json={"status": "rejected"})
    assert answered.status_code == 200, answered.text
    assert answered.json()["method"] == "owner"
    confirmed = await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})
    assert confirmed.status_code == 200

    [cmr] = (await files_of(client, placement["project_id"])).values()
    assert (cmr["method"], cmr["status"]) == ("owner", "rejected")


async def test_a_conversations_answer_makes_its_links_the_owners(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)

    await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})

    page = await client.get(
        "/project-links", params={"project_id": placement["project_id"], "item_kind": "message"}
    )
    assert [link["method"] for link in page.json()["items"]] == ["owner"]


async def test_standing_paper_belongs_to_no_job(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    terms = {"Terms 2026.pdf": b"%PDF-1.7 the carrier's terms"}
    # The same terms came in four conversations: they are the sender's, not one job's.
    for uid in range(1, 5):
        sent = mail(f"<terms{uid}@x>", subject=f"Offer {uid}", attach=terms)
        await arrive(company_id, uid, sent, category=ThreadCategory.NOISE)
    await arrive(company_id, 5, mail(ORDER, attach=terms | CMR), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)

    assert list(await files_of(client, project.id)) == ["CMR 4471.pdf"]


async def test_merged_projects_keep_the_owners_answer_on_a_file(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    # The same note came in the owner's own conversation too, and the owner said it is not of
    # that job.
    await arrive(company_id, 2, mail("<other@x>", subject="Another", attach=CMR), category=None)
    own = await owner_project(company_id, "<other@x>", name="The owner's own")
    note = (await client.get("/project-links", params={"item_kind": "document"})).json()["items"]
    await client.post(
        "/project-links",
        json={"item_kind": "document", "item_id": note[0]["item_id"], "project_id": str(own.id)},
    )
    [held] = (await files_of(client, own.id)).values()
    assert (
        await client.patch(f"/project-links/{held['id']}", json={"status": "rejected"})
    ).status_code == 200

    merged = await client.post(
        f"/projects/{placement['project_id']}/merge", json={"into": str(own.id)}
    )

    assert merged.status_code == 200, merged.text
    [cmr] = (await files_of(client, own.id)).values()
    assert (cmr["method"], cmr["status"]) == ("owner", "rejected")


async def test_a_file_that_becomes_standing_paper_leaves_its_project(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    # The same note comes in three more conversations: it is the sender's paper, not the job's.
    for uid in range(2, 5):
        sent = mail(f"<again{uid}@x>", subject=f"Again {uid}", attach=CMR)
        await arrive(company_id, uid, sent, category=ThreadCategory.NOISE)

    answered = await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})

    assert answered.status_code == 200, answered.text
    assert await files_of(client, placement["project_id"]) == {}


async def test_an_empty_answer_leaves_a_file_link_to_its_mail(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    [cmr] = (await files_of(client, placement["project_id"])).values()

    answered = await client.patch(f"/project-links/{cmr['id']}", json={})

    assert answered.status_code == 200, answered.text
    assert answered.json()["method"] == "mail"


async def test_a_rejected_project_lets_go_of_its_mails_files(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)

    rejected = await client.patch(
        f"/projects/{placement['project_id']}", json={"status": "rejected"}
    )

    assert rejected.status_code == 200, rejected.text
    assert await files_of(client, placement["project_id"]) == {}


async def test_an_archive_read_after_its_mail_was_placed_brings_its_files(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    packed = {"Papers.zip": zip_of({"POD 4471.pdf": pdfs.pdf("Delivered and signed")})}
    placement = await proposed(client, model, attach=packed)
    assert list(await files_of(client, placement["project_id"])) == ["Papers.zip"]

    await documents.read_pending()

    page = await client.get(
        "/project-links", params={"project_id": placement["project_id"], "item_kind": "document"}
    )
    held = page.json()["items"]
    assert len(held) == 2
    assert {link["status"] for link in held} == {"suggested"}


def with_pictures(message_id: str, pictures: dict[str, bytes]) -> bytes:
    """``mail``'s mail with the CMR, carrying these pictures too."""
    message = email.message_from_bytes(mail(message_id, attach=CMR), policy=email.policy.default)
    assert isinstance(message, email.message.EmailMessage)
    for name, data in pictures.items():
        message.add_attachment(data, maintype="image", subtype="png", filename=name)
    return message.as_bytes()


async def test_a_picture_joins_its_mails_project_once_read_with_something_on_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    pictures = {"image001.png": sized_png(40, 40), "slip.png": sized_png(1200, 1600)}
    await arrive(company_id, 1, with_pictures(ORDER, pictures), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [placement] = (await client.get("/placements")).json()["items"]
    project_id = placement["project_id"]
    # Neither picture is read yet: what either holds is not known.
    assert set(await files_of(client, project_id)) == {"CMR 4471.pdf"}

    await documents.read_pending()
    # The icon is too small to hold anything; the photo waits for the vision model.
    assert set(await files_of(client, project_id)) == {"CMR 4471.pdf"}

    async with open_session() as db:
        found = await db.scalars(
            select(MessageAttachment.document_id).where(MessageAttachment.filename == "slip.png")
        )
        slip_id = found.one()
        slip = (await documents.get_documents(db, company_id, [slip_id]))[slip_id]
        slip.text, slip.description = "Plaka No: 34 ABC 12  1.KG: 25860", "A weighbridge slip"
        await documents._was_read(db, slip)  # noqa: SLF001  (what the vision model's reading ends in)
        await db.commit()
    files = await files_of(client, project_id)
    assert set(files) == {"CMR 4471.pdf", "slip.png"}
    assert (files["slip.png"]["method"], files["slip.png"]["status"]) == ("mail", "suggested")

    # A type someone gave says what a picture holds, though nothing could be read off it.
    async with open_session() as db:
        found = await db.scalars(
            select(MessageAttachment.document_id).where(
                MessageAttachment.filename == "image001.png"
            )
        )
        icon_id = found.one()
    typed = await client.patch(f"/documents/{icon_id}", json={"doc_type": "loading_photo"})
    assert typed.status_code == 200, typed.text
    assert set(await files_of(client, project_id)) == {"CMR 4471.pdf", "slip.png", "image001.png"}


async def test_a_picture_the_vision_model_cannot_read_joins_its_mails_project(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await guest_client(client)
    pictures = {"slip.png": sized_png(1200, 1600)}
    await arrive(company_id, 1, with_pictures(ORDER, pictures), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [placement] = (await client.get("/placements")).json()["items"]
    await documents.read_pending()
    assert set(await files_of(client, placement["project_id"])) == {"CMR 4471.pdf"}

    # The vision model answers nothing it can use: what the picture holds is not known.
    monkeypatch.setattr(get_settings(), "vision_pages_per_day", 40)
    monkeypatch.setattr(get_settings(), "agent_second_models", {})
    refusing = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("No.")]))
    monkeypatch.setattr(llm, "model_for", lambda name: refusing)
    assert await documents.read_pending_with_vision() == 1

    assert set(await files_of(client, placement["project_id"])) == {"CMR 4471.pdf", "slip.png"}


async def test_a_file_typed_a_signature_leaves_its_project_and_comes_back_typed_again(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    project_id = placement["project_id"]
    [cmr] = (await files_of(client, project_id)).values()

    typed = await client.patch(f"/documents/{cmr['item_id']}", json={"doc_type": "signature"})

    assert typed.status_code == 200, typed.text
    assert await files_of(client, project_id) == {}
    retyped = await client.patch(f"/documents/{cmr['item_id']}", json={"doc_type": "cmr"})
    assert retyped.status_code == 200, retyped.text
    assert set(await files_of(client, project_id)) == {"CMR 4471.pdf"}
