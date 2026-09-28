import uuid

import httpx
import pytest

from app.modules.projects.models import FieldSource
from app.packs.logistics import service
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def test_a_truck_is_edited_by_its_owner_and_reports_positions(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "Istanbul → Munich"})).json()

    created = await client.post(
        "/shipments",
        json={
            "project_id": project["id"],
            "truck_plate": " 34 abc 12 ",
            "loading_date": "2026-09-29",
        },
    )
    truck = created.json()
    moved = await client.post(
        f"/shipments/{truck['id']}/locations",
        json={"place": "Kapıkule", "raw_text": "Kapıkule'de bekliyoruz", "status": "at_border"},
    )
    listed = await client.get("/shipments", params={"project_id": project["id"]})

    assert created.status_code == 201
    assert truck["truck_plate"] == "34 ABC 12" and truck["status"] == "planned"
    assert truck["field_sources"] == {"truck_plate": "owner", "loading_date": "owner"}
    assert moved.status_code == 201
    [row] = listed.json()
    assert row["last_location"]["place"] == "Kapıkule"


async def test_a_dispatcher_contact_needs_a_dispatcher(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "One"})).json()

    response = await client.post(
        "/shipments", json={"project_id": project["id"], "location_contact": "dispatcher"}
    )
    assert response.status_code == 400 and response.json()["error"]["field"] == "dispatcher_id"


async def test_a_read_plate_waits_for_the_owner_and_is_written_on_accept(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    project = (await client.post("/projects", json={"name": "One"})).json()
    truck = (
        await client.post("/shipments", json={"project_id": project["id"], "truck_plate": "34 A 1"})
    ).json()

    async with open_session() as db:
        await service.propose_shipment_fields(
            db,
            company_id=company_id,
            shipment_id=uuid.UUID(truck["id"]),
            values={"truck_plate": "34 B 2", "driver_name": "Mehmet"},
            source=FieldSource.EXTRACTION,
            evidence=[],
        )
        await db.commit()

    after = (await client.get(f"/shipments/{truck['id']}")).json()
    [proposal] = (await client.get("/field-proposals", params={"target": "shipment"})).json()[
        "items"
    ]
    # The empty field was filled; the owner's plate was not overwritten.
    assert after["truck_plate"] == "34 A 1" and after["driver_name"] == "Mehmet"
    assert after["field_sources"]["driver_name"] == "extraction"
    assert proposal["field"] == "truck_plate" and proposal["value"] == "34 B 2"

    await client.patch(f"/field-proposals/{proposal['id']}", json={"status": "accepted"})
    accepted = (await client.get(f"/shipments/{truck['id']}")).json()
    assert accepted["truck_plate"] == "34 B 2"
    assert accepted["field_sources"]["truck_plate"] == "owner"
