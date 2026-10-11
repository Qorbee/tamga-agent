"""A bill is paid by a method chosen per payment (ADR-0027): by bank transfer, which the owner
makes and Tamga records, in a currency of its own; the vault's rules and the executor never touch
it, and it is settled by the bank's movement matched to it or by the owner's word."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.modules.decisions import service
from app.modules.money import service as money
from app.modules.money.models import AccountKind
from app.modules.money.schemas import AccountRecord, MovementRecord
from app.modules.settlement import executor
from app.packs.logistics import sandbox
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.decisions.test_decisions import Vault, a_sandbox, answer, by_bill, failing
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)
from app.tests.settlement.test_payments import Executor
from app.tests.settlement.test_payments import wallet as wallet  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

BOTH = {"methods": ["vault_usdc", "bank_transfer"], "default_method": "bank_transfer"}


async def by_bank(client: httpx.AsyncClient, **policy: Any) -> dict[str, Any]:
    """A sandbox paying by bank, the delivered truck's bill evaluated."""
    await a_sandbox(client)
    changed = await client.patch("/policy", json=BOTH | policy)
    assert changed.status_code == 200, changed.text
    await service.evaluate_pending()
    return (await by_bill(client))[sandbox.DELIVERED_BILL]


async def events(client: httpx.AsyncClient, decision: dict[str, Any]) -> list[dict[str, Any]]:
    page = await client.get("/decision-events", params={"decision_id": decision["id"]})
    return list(page.json()["items"])


async def test_a_bill_paid_by_bank_leads_with_its_own_currency_and_is_marked_paid(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    delivered = await by_bank(client)

    # Proposed by bank, from the euro account the company holds: no vault rule ran.
    assert delivered["method"] == "bank_transfer" and not delivered["pay_chosen"]
    assert (delivered["pay_currency"], delivered["pay_amount"]) == ("EUR", "1900.000000")
    assert {rule["rule"] for rule in delivered["rules"]}.isdisjoint({"vault_caps", "timelock"})
    assert failing(delivered) == {"approval_limit": "propose"}

    # From the dollar account instead: a new evaluation, logged, the amount at the reference rate.
    switched = await answer(client, delivered, "pay_by", method="bank_transfer", pay_currency="USD")
    assert switched.status_code == 200, switched.text
    in_dollars = switched.json()
    assert in_dollars["evaluation"] == delivered["evaluation"] + 1 and in_dollars["pay_chosen"]
    assert (in_dollars["pay_currency"], in_dollars["pay_amount"]) == ("USD", "2204.000000")
    assert in_dollars["pay_rate_source"] is not None
    # What the bill owes never changes with the currency it is paid in.
    assert (in_dollars["amount"], in_dollars["currency"]) == ("1900.00", "EUR")
    # An answer to the evaluation before is refused.
    assert (await answer(client, delivered, "approve")).status_code == 409

    # Approved, the executor leaves it alone: the owner pays it in the bank.
    assert (await answer(client, in_dollars, "approve")).status_code == 200
    assert await executor.pay_approved() == 0 and wallet.calls == []

    paid = await answer(client, in_dollars, "mark_paid", pay_amount="2190.50", pay_currency="USD")
    assert paid.status_code == 200, paid.text
    assert paid.json()["state"] == "settled"
    [payment] = (await client.get("/payments", params={"decision_id": delivered["id"]})).json()[
        "items"
    ]
    assert (payment["method"], payment["pay_amount"], payment["pay_currency"]) == (
        "bank_transfer",
        "2190.500000",
        "USD",
    )
    assert payment["marked_by"] is not None and payment["usdc_units"] is None
    log = await events(client, delivered)
    assert [event["event"] for event in log] == [
        "proposed",
        "pay_by",
        "proposed",
        "approved",
        "settled",
    ]
    settled = log[-1]["body"]
    # Below the expected figure by the bank's rate: an exchange difference, in the log.
    assert (settled["expected_amount"], settled["fx_difference"]) == ("2204.000000", "-13.500000")
    assert settled["actual_rate"] == "1.152895"
    # Paid, nothing more is taken.
    assert (
        await answer(client, paid.json(), "mark_paid", pay_amount="1", pay_currency="USD")
    ).status_code == 409


async def test_the_vault_is_never_marked_paid_by_hand(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    delivered = await by_bank(client)
    from_vault = (await answer(client, delivered, "pay_by", method="vault_usdc")).json()
    assert (from_vault["method"], from_vault["pay_currency"]) == ("vault_usdc", "USDC")
    assert from_vault["pay_amount"] == "0.220400"
    marked = await answer(client, from_vault, "mark_paid", pay_amount="1900", pay_currency="EUR")
    assert marked.status_code == 409
    # Nor a day not come yet.
    by_bank_again = (await answer(client, from_vault, "pay_by", method="bank_transfer")).json()
    tomorrow = (utcnow() + timedelta(days=2)).date().isoformat()
    later = await answer(
        client, by_bank_again, "mark_paid", pay_amount="1900", pay_currency="EUR", paid_on=tomorrow
    )
    assert later.status_code == 400


async def bank_moves(company_id: uuid.UUID, *amounts: tuple[str, str]) -> None:
    """Movements a bank gave out of the company's euro account, to the delivered truck's firm."""
    today = utcnow().date()
    await money.store(
        company_id,
        "testbank",
        [
            AccountRecord(
                source_ref="acc",
                kind=AccountKind.BANK,
                name="Bank",
                currency="EUR",
                balance=Decimal("10000"),
            )
        ],
        [
            MovementRecord(
                source_ref=ref,
                account_ref="acc",
                booked_on=today,
                amount=Decimal(amount),
                currency="EUR",
                # As a bank writes it: in groups of four.
                counterparty_iban=" ".join(
                    sandbox.DELIVERED.iban[i : i + 4] for i in range(0, 34, 4)
                ).strip(),
            )
            for ref, amount in amounts
        ],
    )


async def test_a_movement_from_the_firm_for_the_amount_settles_the_approved_bill(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    delivered = await by_bank(client)
    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])

    # Before the approval, nothing is matched; then too far from the bill: the owner says.
    await bank_moves(company_id, ("far", "-1500.00"))
    assert (await answer(client, delivered, "approve")).status_code == 200
    assert await service.settle_matched() == 0

    # Within the tolerance, a bank's fee taken: settled by it, the movement linked to the bill.
    await bank_moves(company_id, ("near", "-1880.00"))
    assert await service.settle_matched() == 1
    [payment] = (await client.get("/payments", params={"decision_id": delivered["id"]})).json()[
        "items"
    ]
    assert payment["movement_id"] is not None and payment["marked_by"] is None
    movement = next(
        item
        for item in (await client.get("/money/movements")).json()["items"]
        if item["id"] == payment["movement_id"]
    )
    assert movement["invoice_id"] == delivered["invoice_id"]
    assert movement["category"] == "subcontractor_payment"
    assert (await events(client, delivered))[-1]["body"]["movement_id"] == payment["movement_id"]
    assert await service.settle_matched() == 0
    # The bank gives the movement again, naming no bill: the link stays.
    await bank_moves(company_id, ("far", "-1500.00"), ("near", "-1880.00"))
    again = next(
        item
        for item in (await client.get("/money/movements")).json()["items"]
        if item["id"] == payment["movement_id"]
    )
    assert (again["invoice_id"], again["category"]) == (
        delivered["invoice_id"],
        "subcontractor_payment",
    )


async def test_the_reserve_holds_a_bill_that_would_leave_too_little(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    presets = (await client.get("/policy/reserve-presets")).json()
    careful = next(preset for preset in presets if preset["key"] == "careful")
    # Careful, and a floor above what the three accounts hold together.
    mine = careful | {"key": "own:big", "name": "Big floor", "based_on": "careful"}
    mine["floor"] = "50000.00"
    changed = await client.patch(
        "/policy", json=BOTH | {"save_rule": mine, "reserve_rule": "own:big"}
    )
    assert changed.status_code == 200, changed.text
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["state"] == "held" and failing(delivered)["reserve"] == "hold"

    # The floor lowered, a balance read again moves it only across the line.
    mine["floor"] = "1000.00"
    assert (await client.patch("/policy", json={"save_rule": mine})).status_code == 200
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert "reserve" not in failing(delivered)


async def test_a_movement_that_could_pay_two_bills_pays_neither(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    delivered = await by_bank(client)
    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])
    assert (await answer(client, delivered, "approve")).status_code == 200
    # A second bill from the same firm for the same amount, approved too.
    moving = (await by_bill(client))[sandbox.MOVING_BILL]
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE decisions SET state = 'approved', method = 'bank_transfer', party_id = "
                ":party, amount = 1900, currency = 'EUR', decided_at = now(), decided_by = "
                "(SELECT decided_by FROM decisions WHERE id = :delivered) WHERE id = :moving"
            ),
            {"party": delivered["party_id"], "delivered": delivered["id"], "moving": moving["id"]},
        )
        await db.commit()

    await bank_moves(company_id, ("one", "-1890.00"))

    assert await service.settle_matched() == 0
    assert (await client.get("/payments", params={"decision_id": delivered["id"]})).json()[
        "items"
    ] == []
