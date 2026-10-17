"""The chat's views (ADR-0029): the model names a view from the catalog and its filters, and code
reads every figure and keeps it with the answer (ADR-0035). Nothing the model passes is an
amount."""

import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import TypeAdapter

from app.modules.assistant import views
from app.tests.assistant.test_chats import Script, ask, new_chat, user
from app.tests.assistant.test_chats import model as model  # noqa: F401  (the fixture)
from app.tests.conftest import guest_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

# The only whole numbers a view takes: a firm's handle, and how many weeks or months.
COUNTS = {"firm", "weeks", "months"}


def walk(schema: Any, name: str = "") -> list[tuple[str, str]]:
    """Every (field, type) in a JSON schema, nested ones included."""
    found: list[tuple[str, str]] = []
    if isinstance(schema, dict):
        if isinstance(schema.get("type"), str):
            found.append((name, schema["type"]))
        for key, value in schema.items():
            if key == "properties":
                for field, inner in value.items():
                    found += walk(inner, field)
            else:
                found += walk(value, name)
    elif isinstance(schema, list):
        for item in schema:
            found += walk(item, name)
    return found


def test_no_view_takes_an_amount() -> None:
    schema = TypeAdapter(views.View).json_schema()
    typed = walk(schema)
    assert typed, "the walk found nothing"
    assert not [field for field, kind in typed if kind == "number"]
    assert {field for field, kind in typed if kind == "integer"} <= COUNTS


async def test_a_view_is_shown_by_its_filters_and_a_firm_never_given_is_refused(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await guest_client(client)
    chat_id = await new_chat(client)
    script = model(
        ("show", {"what": {"view": "money_outlook", "weeks": 6}, "title": "Next six weeks"}),
        ("show", {"what": {"view": "decisions", "states": ["held"], "firm": 9}}),
        "The view shows the weeks ahead.",
    )

    await ask(client, chat_id, user("How does the money look?"))

    assert '"view": "money_outlook"' in script.returned(1).replace("'", '"')
    assert "Next six weeks" in script.returned(1) and "'weeks': 6" in script.returned(1)
    assert "Use a firm handle that a search returned." in script.returned(2)
    # The stored run keeps the tool's call and its result, which the client draws the view from.
    shown = (await client.get(f"/chats/{chat_id}/messages")).json()["messages"]
    assert "money_outlook" in str(shown)


async def test_movements_of_one_account_are_shown_only_when_the_words_fit_one(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await guest_client(client)
    for name, currency in (("Office safe", "EUR"), ("Office safe", "USD")):
        made = await client.post(
            "/money/accounts", json={"kind": "cash", "name": name, "currency": currency}
        )
        assert made.status_code == 201, made.text
    euro = next(
        account["id"]
        for account in (await client.get("/money/accounts")).json()["items"]
        if account["currency"] == "EUR"
    )
    chat_id = await new_chat(client)
    script = model(
        ("show", {"what": {"view": "movements", "account": "office"}}),
        ("show", {"what": {"view": "movements", "account": "Office EUR"}}),
        "The view shows the safe's euros.",
    )

    await ask(client, chat_id, user("What went through the office safe in euros?"))

    assert "2 of the company's 2 accounts fit" in script.returned(1)
    assert euro in script.returned(2) and "'account'" not in script.returned(2)


def test_invoice_totals_show_both_sides_unless_one_is_named() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(views.View)
    both = adapter.validate_python({"view": "invoice_totals"})
    payable = adapter.validate_python({"view": "invoice_totals", "direction": "payable"})
    assert isinstance(both, views.Totals) and both.direction == "both"
    assert isinstance(payable, views.Totals) and payable.direction == "payable"


def snapshot_of(returned: str) -> str:
    found = re.search(r"'snapshot': '([0-9a-f-]{36})'", returned)
    assert found, returned
    return found.group(1)


async def test_a_view_keeps_the_figures_it_was_answered_with(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await guest_client(client)
    assert (await client.post("/projects", json={"name": "Munich"})).status_code == 201
    chat_id = await new_chat(client)
    script = model(
        ("show", {"what": {"view": "projects"}}),
        ("show", {"what": {"view": "money_outlook", "weeks": 2}}),
        "Your jobs, and the weeks ahead.",
    )
    await ask(client, chat_id, user("Which jobs do we have?"))
    jobs = snapshot_of(script.returned(1))
    weeks = snapshot_of(script.returned(2))

    # A job added since shows on the page, never in the answer given before it.
    assert (await client.post("/projects", json={"name": "Lyon"})).status_code == 201
    kept = (await client.get(f"/view-snapshots/{jobs}")).json()
    assert kept["view"] == "projects" and kept["data"]["projects"]["total"] == 1
    assert (await client.get("/projects")).json()["total"] == 2
    # Each week's totals, both sides, from the company's day then.
    outlook = (await client.get(f"/view-snapshots/{weeks}")).json()["data"]
    assert len(outlook["totals"]) == 2 * 3 and outlook["today"]

    # Another company never reads it.
    await guest_client(client)
    assert (await client.get(f"/view-snapshots/{jobs}")).status_code == 404
