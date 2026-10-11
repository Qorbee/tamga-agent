"""The payment run (docs/architecture/05): the bills to pay on a day, weighed in order against what
the accounts hold once the reserve keeps its share; approving it is approving each bill."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.modules.decisions import runs, service
from app.packs.logistics import sandbox
from app.tests.decisions.test_decisions import Vault, a_sandbox, by_bill
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


def numbers(items: list[dict[str, Any]]) -> list[str]:
    return [item["decision"]["invoice"]["number"] for item in items]


async def short_cash(client: httpx.AsyncClient) -> dict[str, Any]:
    await a_sandbox(client, scenario="payday")
    await service.evaluate_pending()
    run = await client.get("/payment-runs/current")
    assert run.status_code == 200, run.text
    return dict(run.json())


def test_the_run_falls_on_the_next_pay_day() -> None:
    wednesday = date(2026, 10, 14)
    assert runs.run_day(wednesday, 4) == date(2026, 10, 16)
    assert runs.run_day(wednesday, 2) == wednesday
    assert runs.run_day(wednesday, 0) == date(2026, 10, 19)
    assert runs.run_day(wednesday, None) == wednesday


async def test_the_run_says_which_bills_fit_under_the_reserve(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    run = await short_cash(client)

    today = date.fromisoformat(run["today"])
    assert date.fromisoformat(run["on"]) == runs.run_day(today, 4)
    assert run["currency"] == "EUR" and run["reserve"]["key"] == "steady"
    # The three delivered carriers, the overdue first; the one on the road is never counted.
    assert numbers(run["items"]) == list(sandbox.RUN_BILLS)
    assert sandbox.LATER_BILL not in numbers(run["items"])
    # 4,200 EUR and 900 USD at 1.16; the reserve keeps the bill on the road, due within 2 weeks.
    assert Decimal(run["balance"]) == Decimal("4975.86")
    assert Decimal(run["kept"]) == Decimal("1000.00")
    assert [item["fits"] for item in run["items"]] == [True, True, False]


async def test_every_page_of_decisions_is_read(
    client: httpx.AsyncClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One decision a page: the run still holds every bill due, in its own order.
    monkeypatch.setattr(runs, "_PAGE", 1)
    run = await short_cash(client)
    assert numbers(run["items"]) == list(sandbox.RUN_BILLS)


async def test_approving_the_run_approves_each_bill_and_refuses_a_stale_one(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    run = await short_cash(client)
    first, second, third = (item["decision"] for item in run["items"])

    answered = await client.post(
        "/payment-runs/approvals",
        json={
            "items": [
                {"decision_id": first["id"], "evaluation": first["evaluation"]},
                {"decision_id": second["id"], "evaluation": second["evaluation"]},
                # An evaluation since replaced: refused with the reason, the rest go on.
                {"decision_id": third["id"], "evaluation": third["evaluation"] + 1},
            ]
        },
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["approved"] == [first["id"], second["id"]]
    assert [refusal["decision_id"] for refusal in body["refused"]] == [third["id"]]
    assert "evaluated again" in body["refused"][0]["reason"]

    decisions = await by_bill(client)
    assert decisions[sandbox.RUN_BILLS[0]]["state"] == "approved"
    assert decisions[sandbox.RUN_BILLS[2]]["state"] == "awaiting_owner"
    # Approved bills stay in the run, spent first, until paid.
    again = (await client.get("/payment-runs/current")).json()
    assert [item["decision"]["state"] for item in again["items"]] == [
        "approved",
        "approved",
        "awaiting_owner",
    ]


async def test_a_day_asked_for_before_today_is_today(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    run = await short_cash(client)
    before = (date.fromisoformat(run["today"]) - timedelta(days=3)).isoformat()
    asked = (await client.get("/payment-runs/current", params={"on": before})).json()
    assert asked["on"] == run["today"]
