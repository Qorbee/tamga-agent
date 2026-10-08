"""The assistant's own tools on a judge's sandbox: a project's whole file read with its trucks and
what it lacks, and the draft tools changing Tamga's records as the owner, through the services
the pages use."""

from collections.abc import Callable

import httpx
import pytest

from app.modules.documents import service as documents
from app.modules.playground import service as playground
from app.packs.logistics import sandbox
from app.tests.assistant.test_chats import (
    Script,
    ask,
    model,  # noqa: F401  (the fixture)
    new_chat,
    user,
    words,
)
from app.tests.conftest import guest_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def the_sandbox(client: httpx.AsyncClient) -> str:
    company_id = await guest_client(client)
    await playground.seed(company_id)
    await documents.read_pending()
    [project] = (await client.get("/projects")).json()["items"]
    return str(project["id"])


async def test_a_projects_file_shows_its_trucks_papers_and_what_it_lacks(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await the_sandbox(client)
    script = model(("open", {"handle": sandbox.SALE}), "One truck is proven.")

    answer = await ask(client, await new_chat(client), user("What does the Northwind job lack?"))

    assert words(answer) == "One truck is proven."
    shown = script.returned(1)
    assert f"t1: {sandbox.PLATE_DELIVERED}" in shown and f"t2: {sandbox.PLATE_MOVING}" in shown
    assert "(cmr), confirmed, by the owner, on t1, marked as its proof of delivery" in shown
    assert "Still missing: cmr on t2; proof of delivery on t2" in shown
    assert "Waiting: subcontractor paid" in shown


async def test_the_draft_tools_change_tamgas_records_as_the_owner(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    project_id = await the_sandbox(client)
    script = model(
        ("open", {"handle": sandbox.SALE}),
        # The client's invoice is the first invoice shown, after the one file.
        ("answer_link", {"link": 2, "answer": "rejected"}),
        ("add_truck", {"project": sandbox.SALE, "plate": "34 ABC 12", "driver": "A. Driver"}),
        ("update_project", {"number": sandbox.SALE, "name": "Northwind, the textiles"}),
        ("create_project", {"name": "Bursa to Lyon"}),
        "Done.",
    )

    answer = await ask(client, await new_chat(client), user("Tidy the Northwind job up"))

    assert words(answer) == "Done."
    assert script.returned(2) == "l2 is rejected now."
    assert script.returned(3).startswith("Added t3")
    links = (await client.get("/project-links", params={"project_id": project_id})).json()["items"]
    sale = [
        link for link in links if link["item_kind"] == "invoice" and link["status"] == "rejected"
    ]
    assert len(sale) == 1 and sale[0]["method"] == "owner"
    trucks = (await client.get("/shipments", params={"project_id": project_id})).json()
    assert {truck["truck_plate"] for truck in trucks} >= {"34 ABC 12"}
    projects = {
        project["name"]
        for project in (
            await client.get("/projects", params={"status": ["active", "draft"]})
        ).json()["items"]
    }
    assert {"Northwind, the textiles", "Bursa to Lyon"} <= projects


async def test_a_draft_tool_refused_by_the_service_says_so(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await the_sandbox(client)
    script = model(
        ("open", {"handle": sandbox.SALE}),
        # A handle or a number the run was never given reaches nothing.
        ("answer_link", {"link": 99, "answer": "confirmed"}),
        ("update_project", {"number": "P-NOPE", "status": "closed"}),
        "Nothing changed.",
    )

    await ask(client, await new_chat(client), user("Confirm everything"))

    assert script.returned(2) == "No such link: use a handle that open returned."
    assert script.returned(3) == "No such project: use a number that a tool returned."
