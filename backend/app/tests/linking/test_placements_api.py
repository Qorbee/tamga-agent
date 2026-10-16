import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.modules.conversations import repository as mail_repository
from app.modules.conversations import service as conversations
from app.modules.conversations.models import ThreadCategory
from app.modules.linking import evidence, repository, service
from app.modules.linking.models import Outcome, ProposalBand
from app.modules.projects.models import LinkStatus, ProjectStatus
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import (
    NEW_PROJECT,
    Script,
    answer,
    arrive,
    links,
    mail,
    model,  # noqa: F401  (the fixture)
    name_projects,
    owner_project,
    the_projects,
    tool,
)
from app.tests.linking.test_placing import (
    placement as placement_of,
)

# The mail these tests store goes to the storage service, in the bucket the tests make.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

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
    read = {key: value for key, value in placement["reading"].items() if value is not None}
    assert read == {"origin": "Istanbul", "cargo": "steel coils"}
    # What backs it, weighed by code: the route is read, the client and the first day are not;
    # nothing was answered yet to measure it by.
    assert placement["evidence"] == {
        "band": "partial",
        "missing": ["client", "first_day"],
        "measured": None,
    }
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
    # It is read by its id too, and only by its own company.
    one = await client.get(f"/placements/{placement['id']}")
    assert one.status_code == 200 and one.json()["reason"] == "why"
    await guest_client(client)
    assert (await client.get(f"/placements/{placement['id']}")).status_code == 404


async def test_a_proposal_says_how_often_the_owner_kept_those_weighed_the_same(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evidence, "MEASURED_FROM", 2)
    first = await proposed(client, model)
    await client.patch(f"/projects/{first['project_id']}", json={"status": "rejected"})
    company_id = uuid.UUID(first["company_id"])

    async def propose(uid: int, subject: str) -> dict[str, Any]:
        await arrive(
            company_id,
            uid,
            mail(f"<{uid}@x>", subject=subject, date=f"Tue, 0{uid} Sep 2026 09:00:00 +0300"),
            category=ThreadCategory.WORK,
        )
        model(tool("search_projects", words="Stone"), NEW_PROJECT)
        assert await service.place_pending() == 1
        page = (await client.get("/placements", params={"outcome": "proposed"})).json()
        return next(item for item in page["items"] if item["conversation"]["subject"] == subject)

    # One answered is too few to say anything.
    assert (await propose(3, "Lyon load"))["evidence"]["measured"] is None
    lyon = (await client.get("/placements", params={"outcome": "proposed"})).json()["items"]
    [kept] = [item for item in lyon if item["conversation"]["subject"] == "Lyon load"]
    await client.patch(f"/projects/{kept['project_id']}", json={"status": "active"})
    # Two of its band answered: one kept, one refused. Another band's answers would not count.
    assert (await propose(4, "Turin load"))["evidence"]["measured"] == {"answered": 2, "kept": 1}
    # Each is measured by the band it had when proposed, kept as it was.
    async with open_session() as db:
        kept_bands = await db.scalars(
            select(ProposalBand.band).where(ProposalBand.company_id == company_id)
        )
        assert sorted(kept_bands.all()) == ["partial"] * 3


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
        "Ada Stone · 03.08.2026"
    ]


async def test_a_confirmed_conversation_is_moved_to_another_project_and_back(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    url, was = f"/placements/{placement['id']}", placement["project_id"]
    await arrive(company_id, 3, mail("<other@x>", subject="Another"), category=None)
    own = await owner_project(company_id, "<other@x>", name="The owner's own")
    assert (await client.patch(url, json={"links": "confirmed"})).status_code == 200

    # Seen in a project it has since left, it is not moved.
    stale = {"project_id": was, "from_project_id": str(own.id)}
    assert (await client.patch(url, json=stale)).status_code == 409
    assert (await client.patch(url, json={"from_project_id": was})).status_code == 422
    moved = await client.patch(url, json={"project_id": str(own.id), "from_project_id": was})

    assert moved.status_code == 200, moved.text
    answered = moved.json()
    assert (answered["outcome"], answered["project_id"]) == ("linked", str(own.id))
    # What the mail says of its job goes with it.
    read = {key: value for key, value in answered["reading"].items() if value is not None}
    assert read == {"origin": "Istanbul", "cargo": "steel coils"}
    held = await links(company_id, ORDER)
    assert sorted((link.project_id == own.id, link.status) for link in held) == [
        (False, LinkStatus.REJECTED),
        (False, LinkStatus.REJECTED),
        (True, LinkStatus.CONFIRMED),
        (True, LinkStatus.CONFIRMED),
    ]
    # Moved back, the owner's own earlier no gives way to the owner's yes.
    back = await client.patch(url, json={"project_id": was, "from_project_id": str(own.id)})
    assert back.status_code == 200, back.text
    held = await links(company_id, ORDER)
    assert sorted((link.project_id == own.id, link.status) for link in held) == [
        (False, LinkStatus.CONFIRMED),
        (False, LinkStatus.CONFIRMED),
        (True, LinkStatus.REJECTED),
        (True, LinkStatus.REJECTED),
    ]


async def test_two_projects_that_are_one_job_are_merged_and_everything_follows(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    company_id = uuid.UUID(placement["company_id"])
    gone = (await client.get(f"/projects/{placement['project_id']}")).json()
    # The same job, proposed a second time from another conversation, and a third conversation
    # that stands between the two.
    await arrive(company_id, 3, mail("<again@x>", subject="Munich"), category=ThreadCategory.WORK)
    model(
        answer(
            "new_project",
            new_project={"client": "Stone GmbH", "destination": "Munich"},
        )
    )
    assert await service.place_pending() == 1
    stays = next(p for p in await the_projects(company_id) if str(p.id) != gone["id"])
    await arrive(company_id, 4, mail("<which@x>", subject="POD"), category=ThreadCategory.WORK)
    model(
        tool("search_projects", words="Stone"),
        answer("ask", alternatives=[gone["number"], stays.number]),
    )
    assert await service.place_pending() == 1
    confirmed = await client.patch(f"/placements/{placement['id']}", json={"links": "confirmed"})
    assert confirmed.status_code == 200
    assert (await client.patch(f"/projects/{gone['id']}", json={"status": "active"})).is_success

    asked_at = (await placement_of(company_id, "<which@x>")).placed_at

    same = await client.post(f"/projects/{gone['id']}/merge", json={"into": gone["id"]})
    unknown = await client.post(f"/projects/{gone['id']}/merge", json={"into": str(uuid.uuid7())})
    merged = await client.post(f"/projects/{gone['id']}/merge", json={"into": str(stays.id)})

    assert (same.status_code, unknown.status_code) == (400, 404)
    assert merged.status_code == 200, merged.text
    kept = merged.json()
    # One project, found by either number, confirmed as the one that went was, and named again
    # from what all its conversations say of the job together.
    assert (await client.get(f"/projects/{gone['id']}")).status_code == 404
    assert kept["id"] == str(stays.id) and kept["aliases"] == [gone["number"]]
    assert (kept["status"], kept["confirmed_links"], kept["suggested_links"]) == ("active", 2, 2)
    [named] = await name_projects(company_id)
    assert named.name == "Stone GmbH · Istanbul → Munich"
    found = (await client.get("/projects", params={"q": gone["number"]})).json()
    assert [item["id"] for item in found["items"]] == [kept["id"]]
    placed = (await client.get("/placements", params={"project_id": kept["id"]})).json()
    # The conversation that stood between the two is suggested in the one that is left.
    assert sorted(item["outcome"] for item in placed["items"]) == ["linked", "linked", "proposed"]
    assert [link.project_id for link in await links(company_id, "<which@x>")] == [stays.id]
    assert [link.status for link in await links(company_id, "<which@x>")] == [LinkStatus.SUGGESTED]
    # A run that read it before the merge would decide on a project that is gone: the placement
    # is stamped anew, so that decision is dropped.
    stamped = (await placement_of(company_id, "<which@x>")).placed_at
    assert asked_at is not None and stamped is not None and stamped > asked_at
    # Another sandbox cannot merge these.
    await guest_client(client)
    theirs = await client.post(f"/projects/{kept['id']}/merge", json={"into": gone["id"]})
    assert theirs.status_code == 404
