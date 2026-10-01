import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.modules.conversations import repository as mail_repository
from app.modules.conversations import service as conversations
from app.modules.conversations.models import ThreadCategory
from app.modules.linking import repository, service
from app.modules.linking.models import Outcome
from app.modules.projects.models import LinkStatus, ProjectStatus
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import (
    NEW_PROJECT,
    Script,
    arrive,
    links,
    mail,
    model,  # noqa: F401  (the fixture)
    owner_project,
    the_projects,
    tool,
)

pytestmark = pytest.mark.integration

ORDER = "<order@x>"


async def proposed(client: httpx.AsyncClient, model: Callable[..., Script]) -> dict[str, Any]:  # noqa: F811
    """A sandbox with one conversation of two mails that the linker proposed a project for.
    Returns the placement as the API gives it."""
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail(ORDER), category=ThreadCategory.WORK)
    reply = mail("<reply@x>", references=ORDER, date="Mon, 03 Aug 2026 11:00:00 +0300")
    await arrive(company_id, 2, reply, category=ThreadCategory.WORK)
    model(tool("search_projects", words="Stone"), NEW_PROJECT)
    assert await service.place_pending() == 1
    page = (await client.get("/placements")).json()
    assert page["total"] == 1
    return {"company_id": str(company_id), **page["items"][0]}


async def test_a_placement_says_what_the_conversation_is_where_it_went_and_why(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    project = (await client.get(f"/projects/{placement['project_id']}")).json()

    assert placement["outcome"] == "proposed" and placement["placed_by"] == "model:linker"
    assert placement["reason"] == "why" and placement["confidence"] == 0.8
    assert placement["reading"] == {"origin": "Istanbul", "cargo": "steel coils"}
    assert placement["suggested_links"] == 2
    conversation = placement["conversation"]
    assert conversation["subject"] == "Munich load" and conversation["message_count"] == 2
    # The project counts what waits on it, and is found by that.
    assert project["suggested_links"] == 2 and project["confirmed_links"] == 0
    waiting = (await client.get("/projects", params={"suggested": True})).json()
    assert [item["id"] for item in waiting["items"]] == [project["id"]]
    assert (await client.get("/projects", params={"suggested": False})).json()["total"] == 0
    by_project = await client.get("/placements", params={"project_id": project["id"]})
    assert by_project.json()["total"] == 1
    assert (await client.get("/placements", params={"outcome": "asked"})).json()["total"] == 0


async def test_confirming_a_placement_confirms_every_link_that_waits(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)

    response = await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})

    assert response.status_code == 200, response.text
    assert response.json()["suggested_links"] == 0
    assert response.json()["outcome"] == "proposed"
    project = (await client.get(f"/projects/{placement['project_id']}")).json()
    assert project["confirmed_links"] == 2 and project["suggested_links"] == 0


async def test_rejecting_a_placement_rejects_its_links_and_leaves_it_unplaced(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)

    response = await client.patch(f"/placements/{placement['id']}", json={"links": "rejected"})

    assert response.status_code == 200, response.text
    answered = response.json()
    assert answered["outcome"] == "unplaced" and answered["placed_by"] == "owner"
    assert answered["project_id"] is None and answered["reason"] == ""
    held = await links(uuid.UUID(placement["company_id"]), ORDER)
    assert [link.status for link in held] == [LinkStatus.REJECTED, LinkStatus.REJECTED]
    # Nothing new came: the linker does not read it again and suggest the same.
    assert await service.place_pending() == 0


async def test_the_owner_names_the_project_and_what_was_suggested_elsewhere_is_rejected(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    await arrive(company_id, 3, mail("<other@x>", subject="Another"), category=None)
    own = await owner_project(company_id, "<other@x>", name="The owner's own")

    response = await client.patch(
        f"/placements/{placement['id']}", json={"project_id": str(own.id)}
    )

    assert response.status_code == 200, response.text
    answered = response.json()
    assert answered["outcome"] == "linked" and answered["placed_by"] == "owner"
    assert answered["project_id"] == str(own.id) and answered["suggested_links"] == 0
    held = await links(company_id, ORDER)
    assert sorted((link.project_id == own.id, link.status) for link in held) == [
        (False, LinkStatus.REJECTED),
        (False, LinkStatus.REJECTED),
        (True, LinkStatus.CONFIRMED),
        (True, LinkStatus.CONFIRMED),
    ]
    statuses = {project.id: project.status for project in await the_projects(company_id)}
    # A draft the owner ties mail to becomes active; the proposal stays for the owner to reject.
    assert statuses[own.id] is ProjectStatus.ACTIVE


async def test_a_decision_needs_one_answer_and_a_project_of_the_company(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    url = f"/placements/{placement['id']}"

    assert (await client.patch(url, json={})).status_code == 422
    both = {"links": "confirmed", "project_id": placement["project_id"]}
    assert (await client.patch(url, json=both)).status_code == 422
    unknown = await client.patch(url, json={"project_id": str(uuid.uuid7())})
    assert unknown.status_code == 404
    # Another sandbox sees neither the placement nor a way to answer it.
    await guest_client(client)
    assert (await client.get("/placements")).json()["total"] == 0
    assert (await client.patch(url, json={"links": "confirmed"})).status_code == 404


async def test_an_answer_given_twice_is_refused_rather_than_set_apart_from_its_links(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    url = f"/placements/{placement['id']}"
    await arrive(company_id, 3, mail("<other@x>", subject="Another"), category=None)
    own = await owner_project(company_id, "<other@x>", name="The owner's own")
    assert (await client.patch(url, json={"links": "confirmed"})).status_code == 200

    # A second tab, still showing the question: every mail is confirmed where it was placed.
    elsewhere = await client.patch(url, json={"project_id": str(own.id)})
    rejected = await client.patch(url, json={"links": "rejected"})

    assert elsewhere.status_code == 409 and rejected.status_code == 409
    kept = (await client.get("/placements", params={"project_id": placement["project_id"]})).json()
    assert kept["items"][0]["outcome"] == "proposed"
    held = await links(company_id, ORDER)
    assert [link.status for link in held] == [LinkStatus.CONFIRMED, LinkStatus.CONFIRMED]
    # Confirming again changes nothing and is no error.
    assert (await client.patch(url, json={"links": "confirmed"})).status_code == 200


async def test_an_answer_given_while_the_model_read_is_not_overwritten(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    async with open_session() as db:
        key = await mail_repository.thread_of(db, company_id, ORDER)
        assert key is not None
        row = await mail_repository.get_thread(db, company_id, key)
        assert row is not None
        thread = await conversations.read_thread(db, company_id, row.id)
        assert thread is not None
        found = await service._look(db, company_id, thread)
        seen = await repository.get(db, company_id, thread.id)
        assert seen is not None
        seen_at = seen.placed_at
    # The owner answers while the run, which saw the placement as it was, is still reading.
    url = f"/placements/{placement['id']}"
    assert (await client.patch(url, json={"links": "rejected"})).status_code == 200

    stale = service._Decision(Outcome.PROPOSED, "model:linker", new_name="A stale proposal")
    async with open_session() as db:
        applied = await service._apply(db, company_id, thread, found, stale, seen_at=seen_at)
        await db.commit()

    assert not applied
    answered = (await client.get("/placements")).json()["items"][0]
    assert answered["outcome"] == "unplaced" and answered["placed_by"] == "owner"
    assert [project.name for project in await the_projects(company_id)] == [
        "Stone, Istanbul to Munich"
    ]
