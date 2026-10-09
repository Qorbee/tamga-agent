"""A payable is decided by code (ADR-0002): evaluated from the books, the confirmed links, the
proof of delivery and the vault, evaluated again only when that changed, answered by the owner
for the evaluation in force, and logged in one hash chain per company."""

import dataclasses
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.decisions import service
from app.modules.playground import service as playground
from app.modules.settlement.models import AccountState, PayeeWallet, VaultAccount
from app.packs.logistics import sandbox
from app.platform import chain
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.fx import rates

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


class Vault:
    """The vault as the chain would answer for a funded sandbox."""

    def __init__(self) -> None:
        self.state = chain.CompanyState(
            owner="0x" + "1" * 40,
            timelock_seconds=120,
            nonce=0,
            balance=500_000,
            per_payment_cap=300_000,
            daily_cap=500_000,
            left_today=500_000,
        )
        self.paid: set[bytes] = set()

    async def company_state(self, company_id: uuid.UUID) -> chain.CompanyState | None:
        return self.state

    async def payee_active_from(self, company_id: uuid.UUID, payee: str) -> int | None:
        return 1

    async def is_paid(self, company_id: uuid.UUID, key: bytes) -> bool:
        return key in self.paid


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> Vault:
    stand_in = Vault()
    for name in ("company_state", "payee_active_from", "is_paid"):
        monkeypatch.setattr(chain, name, getattr(stand_in, name))
    return stand_in


async def a_sandbox(client: httpx.AsyncClient, *, on_the_vault: bool = True) -> uuid.UUID:
    """The judges' scenario, its account open on the vault with a wallet for each subcontractor."""
    company_id = await guest_client(client)
    await playground.seed(company_id)
    async with open_session() as db:
        # Where the vault is configured, the session queued an opening of its own.
        await db.execute(
            text("DELETE FROM vault_accounts WHERE company_id = :id"), {"id": company_id}
        )
        await db.commit()
    if on_the_vault:
        async with open_session() as db:
            db.add(
                VaultAccount(
                    company_id=company_id,
                    state=AccountState.OPEN,
                    timelock_seconds=120,
                    per_payment_cap=300_000,
                    daily_cap=500_000,
                    deposit=500_000,
                    transactions={},
                )
            )
            for party_id in await books.payable_parties(db, company_id):
                db.add(
                    PayeeWallet(
                        company_id=company_id,
                        party_id=party_id,
                        circle_wallet_id=f"wallet-{party_id}",
                        address="0x" + party_id.hex.rjust(40, "0"),
                    )
                )
            await db.commit()
    return company_id


async def by_bill(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """The company's decisions by their bill's number."""
    numbers = {
        bill["id"]: bill["number"] for bill in (await client.get("/invoices")).json()["items"]
    }
    page = (await client.get("/decisions")).json()
    return {numbers[decision["invoice_id"]]: decision for decision in page["items"]}


def decisions_of_moving(decisions: dict[str, dict[str, Any]]) -> str:
    """The bill of the truck on the road."""
    return str(decisions[sandbox.MOVING_BILL]["invoice_id"])


def failing(decision: dict[str, Any]) -> dict[str, str]:
    return {rule["rule"]: rule["outcome"] for rule in decision["rules"] if rule["outcome"] != "ok"}


async def answer(
    client: httpx.AsyncClient, decision: dict[str, Any], action: str, **more: Any
) -> httpx.Response:
    return await client.patch(
        f"/decisions/{decision['id']}",
        json={"action": action, "evaluation": decision["evaluation"], **more},
    )


async def test_the_delivered_truck_waits_for_the_owner_and_the_one_on_the_road_is_held(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    company_id = await a_sandbox(client)

    assert await service.evaluate_pending() == 2

    decisions = await by_bill(client)
    delivered, moving = decisions[sandbox.DELIVERED_BILL], decisions[sandbox.MOVING_BILL]
    assert delivered["state"] == "awaiting_owner" and delivered["evaluation"] == 1
    assert failing(delivered) == {"approval_limit": "propose"}
    # 1,900 euros at the mirror's rate (ADR-0019), in USDC's smallest units.
    assert (delivered["amount"], delivered["currency"]) == ("1900.00", "EUR")
    assert delivered["rate"] == "0.000116000000" and delivered["usdc_units"] == 220_400
    assert moving["state"] == "held"
    assert failing(moving) == {"proof_of_delivery": "hold", "approval_limit": "propose"}
    # With its bill, for a page that lists them.
    expanded = (await client.get("/decisions", params={"expand": "invoice"})).json()["items"]
    assert {item["invoice"]["number"] for item in expanded} == {
        sandbox.DELIVERED_BILL,
        sandbox.MOVING_BILL,
    }
    # The newest bill first, or the oldest: the one on the road was sent four days later.
    newest = (await client.get("/decisions", params={"sort": "-issued_on"})).json()["items"]
    assert [item["id"] for item in newest] == [moving["id"], delivered["id"]]
    oldest = (await client.get("/decisions", params={"sort": "issued_on"})).json()["items"]
    assert [item["id"] for item in oldest] == [delivered["id"], moving["id"]]
    assert delivered["issued_on"] < moving["issued_on"]

    # Nothing changed: nothing is evaluated again, and the log does not grow.
    assert await service.evaluate_pending() == 0
    log = (await client.get("/decision-events")).json()
    assert [event["event"] for event in log["items"]] == ["proposed", "proposed"]
    assert log["items"][0]["previous_hash"] == service.GENESIS
    assert log["items"][1]["previous_hash"] == log["items"][0]["hash"]
    async with open_session() as db:
        assert await service.broken_link(db, company_id) is None

    # The books correct a date: the order follows, though nothing the rules read changed.
    async with open_session() as db:
        await db.execute(
            text("UPDATE invoices SET issue_date = issue_date - 30 WHERE id = :id"),
            {"id": moving["invoice_id"]},
        )
        await db.commit()
    assert await service.evaluate_pending() == 0
    newest = (await client.get("/decisions", params={"sort": "-issued_on"})).json()["items"]
    assert [item["id"] for item in newest] == [delivered["id"], moving["id"]]


async def test_an_answer_names_the_evaluation_it_answers(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    company_id = await a_sandbox(client)
    await service.evaluate_pending()
    decisions = await by_bill(client)
    delivered, moving = decisions[sandbox.DELIVERED_BILL], decisions[sandbox.MOVING_BILL]

    stale = await client.patch(
        f"/decisions/{delivered['id']}", json={"action": "approve", "evaluation": 2}
    )
    assert stale.status_code == 409, stale.text
    # A rule holds the truck on the road: no approval passes it.
    assert (await answer(client, moving, "approve")).status_code == 409

    approved = await answer(client, delivered, "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["decided_by"] is not None
    # Approved, it is the executor's: no evaluation moves it, and it is answered once. The bill
    # still held is evaluated again, since the vault's day changed.
    vault.state = dataclasses.replace(vault.state, left_today=100_000)
    assert await service.evaluate_pending() == 1
    decisions = await by_bill(client)
    assert decisions[sandbox.DELIVERED_BILL]["evaluation"] == 1
    assert (await answer(client, approved.json(), "reject")).status_code == 409

    # The owner's hold stands through evaluations, until the owner answers again.
    moving = decisions[sandbox.MOVING_BILL]
    assert moving["evaluation"] == 2
    held = await answer(client, moving, "hold")
    assert held.status_code == 200 and held.json()["decided_by"] is not None
    vault.state = dataclasses.replace(vault.state, left_today=500_000)
    assert await service.evaluate_pending() == 0
    events = (await client.get("/decision-events")).json()["items"]
    assert [event["event"] for event in events] == [
        "proposed",
        "proposed",
        "approved",
        "proposed",
        "held",
    ]
    assert events[2]["user_id"] is not None and events[2]["body"]["usdc_units"] == 220_400
    async with open_session() as db:
        assert await service.broken_link(db, company_id) is None


async def test_a_new_amount_is_a_new_proposal_back_to_the_owner(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]

    edited = await answer(client, delivered, "edit", amount="1000.00")

    assert edited.status_code == 200, edited.text
    part = edited.json()
    assert (part["evaluation"], part["amount"], part["usdc_units"]) == (2, "1000.00", 116_000)
    assert part["state"] == "awaiting_owner" and part["amount_edited"] is True
    # The card the owner had open answered the evaluation before.
    assert (await answer(client, delivered, "approve")).status_code == 409
    # Kept through the next evaluations, and more than is left is held.
    assert await service.evaluate_pending() == 0
    more = await answer(client, part, "edit", amount="2500.00")
    assert more.json()["state"] == "held" and failing(more.json())["amount"] == "hold"
    events = (await client.get("/decision-events")).json()["items"]
    assert [event["event"] for event in events][-4:] == ["edited", "proposed", "edited", "proposed"]
    # An edit comes with its amount, and only an edit does.
    refused = await client.patch(
        f"/decisions/{part['id']}", json={"action": "approve", "evaluation": 3, "amount": "5.00"}
    )
    assert refused.status_code == 422


async def test_what_changed_is_evaluated_again(client: httpx.AsyncClient, vault: Vault) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()

    # The vault's day is nearly spent: the delivered bill waits for tomorrow.
    vault.state = dataclasses.replace(vault.state, left_today=100_000)
    assert await service.evaluate_pending() == 2
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["state"] == "held" and delivered["evaluation"] == 2
    assert failing(delivered)["vault_caps"] == "hold"

    # A new policy is part of what a decision was made from.
    policy = await client.put(
        "/policy", json={"currency": "USD", "approval_limit": "5000.00", "pay_when_paid": True}
    )
    assert policy.status_code == 200 and policy.json()["version"] == 1
    vault.state = dataclasses.replace(vault.state, left_today=500_000)
    assert await service.evaluate_pending() == 2
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    # Within the limit, but no auto-pay rule exists: it still waits for the owner.
    assert delivered["state"] == "awaiting_owner" and failing(delivered) == {}
    assert delivered["policy_version"] == 1

    # The books move the bill to another party: a new proposal, never an approval of the old.
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE invoices SET party_id = (SELECT party_id FROM invoices WHERE id = :other) "
                "WHERE id = :id"
            ),
            {"id": delivered["invoice_id"], "other": decisions_of_moving(await by_bill(client))},
        )
        await db.commit()
    assert await service.evaluate_pending() == 1
    moved = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert moved["evaluation"] == delivered["evaluation"] + 1
    assert moved["party_id"] != delivered["party_id"]
    delivered = moved

    # The bill is paid outside Tamga meanwhile: the decision no longer waits for an approval.
    async with open_session() as db:
        await db.execute(
            text("UPDATE invoices SET remaining_amount = 0 WHERE id = :id"),
            {"id": delivered["invoice_id"]},
        )
        await db.commit()
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["state"] == "paid_in_books" and failing(delivered)["amount"] == "hold"


async def test_without_the_vault_or_a_rate_nothing_waits_for_an_approval(
    client: httpx.AsyncClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    await a_sandbox(client, on_the_vault=False)
    monkeypatch.setattr(get_settings(), "mirror_rates", {"USD": get_settings().mirror_rates["USD"]})

    await service.evaluate_pending()

    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["state"] == "held" and delivered["usdc_units"] is None
    assert failing(delivered)["currency"] == "hold"
    assert failing(delivered)["vault_account"] == "hold"
    # A policy is written in a currency the country's reference rates quote, or not at all.
    refused = await client.put(
        "/policy", json={"currency": "XYZ", "approval_limit": "0", "pay_when_paid": True}
    )
    assert refused.status_code == 400


async def test_a_days_rate_moves_a_decision_only_across_a_limit(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    today = utcnow().date()
    await a_sandbox(client)
    limit = {"currency": "USD", "approval_limit": "5000.00", "pay_when_paid": True}
    assert (await client.put("/policy", json=limit)).status_code == 200
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    # 1,900 euros are 2,204 dollars, within the limit: the rate, its day and source are kept.
    assert failing(delivered) == {}
    assert delivered["reference_rate"] == "1.1600000000"
    assert (delivered["reference_rate_on"], delivered["reference_source"]) == (
        today.isoformat(),
        rates.NAME,
    )

    async def rate(euro: str) -> None:
        async with open_session() as db:
            await db.execute(
                text("UPDATE fx_rates SET rate = :rate WHERE currency = 'EUR'"), {"rate": euro}
            )
            await db.commit()

    # A new rate that keeps it within the limit opens no new evaluation, so an approval of the
    # one in force still stands.
    await rate("1.20")
    assert await service.evaluate_pending() == 0
    # One that takes them above does, the bill on the road too.
    await rate("3.00")
    assert await service.evaluate_pending() == 2
    moved = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert moved["evaluation"] == delivered["evaluation"] + 1
    assert failing(moved) == {"approval_limit": "propose"}
    assert moved["reference_rate"] == "3.0000000000"


async def test_a_bill_the_vault_paid_is_rejected(client: httpx.AsyncClient, vault: Vault) -> None:
    await a_sandbox(client)
    delivered_id = next(
        bill["id"]
        for bill in (await client.get("/invoices")).json()["items"]
        if bill["number"] == sandbox.DELIVERED_BILL
    )
    vault.paid.add(service.payment_key(uuid.UUID(delivered_id)))

    await service.evaluate_pending()

    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert delivered["state"] == "rejected" and failing(delivered)["duplicate"] == "reject"


async def test_the_logs_chain_breaks_where_a_row_was_changed(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    company_id = await a_sandbox(client)
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    await answer(client, delivered, "approve")
    [newest, *_] = (await client.get("/decision-events", params={"sort": "-seq"})).json()["items"]
    holds = (await client.get("/decision-chain")).json()
    assert holds == {"length": 3, "head": newest["hash"], "broken_at": None, "anchor": None}

    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE decision_log SET body = jsonb_set(body, '{usdc_units}', '999999') "
                "WHERE company_id = :id AND seq = 3"
            ),
            {"id": company_id},
        )
        await db.commit()
        assert await service.broken_link(db, company_id) == 3
    assert (await client.get("/decision-chain")).json()["broken_at"] == 3


async def test_only_the_owner_answers_and_a_sandbox_goes_with_its_decisions(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    company_id = await a_sandbox(client)
    await service.evaluate_pending()
    async with open_session() as db:
        await db.execute(
            text("UPDATE memberships SET role = 'ops' WHERE company_id = :id"), {"id": company_id}
        )
        await db.commit()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert (await answer(client, delivered, "approve")).status_code == 403
    assert (
        await client.put(
            "/policy", json={"currency": "USD", "approval_limit": "0", "pay_when_paid": True}
        )
    ).status_code == 403

    async with open_session() as db:
        await companies.delete_sandbox(db, company_id)
        await db.commit()
        left = await db.scalar(
            text("SELECT count(*) FROM decision_log WHERE company_id = :id"), {"id": company_id}
        )
    assert left == 0


async def test_a_cancelled_bill_and_a_second_booking_are_never_approved(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()
    decisions = await by_bill(client)
    delivered, moving = decisions[sandbox.DELIVERED_BILL], decisions[sandbox.MOVING_BILL]

    # The same bill booked twice: both are evaluated before either is approved.
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE invoices SET number = :number, party_id = "
                "(SELECT party_id FROM invoices WHERE id = :delivered) WHERE id = :moving"
            ),
            {
                "number": sandbox.DELIVERED_BILL,
                "delivered": delivered["invoice_id"],
                "moving": moving["invoice_id"],
            },
        )
        # The firm's account owes both, as the books show a bill booked twice: else the account
        # holds the older one.
        await db.execute(
            text(
                "UPDATE parties SET balance = balance * 2 "
                "WHERE id = (SELECT party_id FROM invoices WHERE id = :delivered)"
            ),
            {"delivered": delivered["invoice_id"]},
        )
        # On the delivered truck, whose proof is in.
        await db.execute(
            text(
                "UPDATE project_links SET shipment_id = (SELECT shipment_id FROM project_links "
                "WHERE item_id = :delivered) WHERE item_id = :moving"
            ),
            {"delivered": delivered["invoice_id"], "moving": moving["invoice_id"]},
        )
        await db.commit()
    await service.evaluate_pending()
    twins = [
        decision
        for decision in (await client.get("/decisions")).json()["items"]
        if decision["state"] == "awaiting_owner"
    ]
    assert (await answer(client, twins[0], "approve")).status_code == 200
    # Read again under the lock: the second approval sees the first, and is a new evaluation.
    refused = await answer(client, twins[1], "approve")
    assert refused.status_code == 409, refused.text
    second = (await client.get(f"/decisions/{twins[1]['id']}")).json()
    assert second["state"] == "rejected" and second["evaluation"] == twins[1]["evaluation"] + 1
    assert any(r["rule"] == "duplicate" and r["outcome"] == "reject" for r in second["rules"])

    # Cancelled in the books, though something is left on it: held, never approvable.
    async with open_session() as db:
        await db.execute(
            text("UPDATE invoices SET cancelled = true WHERE id = :id"),
            {"id": twins[1]["invoice_id"]},
        )
        await db.commit()
    await service.evaluate_pending()
    cancelled = (await client.get(f"/decisions/{twins[1]['id']}")).json()
    assert cancelled["state"] in ("held", "rejected")
    assert any(
        rule["rule"] == "amount" and rule["outcome"] == "hold" for rule in cancelled["rules"]
    )


async def test_a_held_bill_is_read_again_before_it_is_approved_and_one_with_no_party_is_held(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    held = await answer(client, delivered, "hold")
    assert held.status_code == 200

    # The owner's hold stops every pass; the books cancel the bill meanwhile.
    async with open_session() as db:
        await db.execute(
            text("UPDATE invoices SET cancelled = true WHERE id = :id"),
            {"id": delivered["invoice_id"]},
        )
        await db.commit()
    assert await service.evaluate_pending() == 0
    refused = await answer(client, held.json(), "approve")
    assert refused.status_code == 409
    again = (await client.get(f"/decisions/{delivered['id']}")).json()
    assert again["state"] == "held" and again["decided_by"] is None
    assert failing(again)["amount"] == "hold"

    # The books clear the moving bill's party: it is held, never left as it was.
    moving = (await by_bill(client))[sandbox.MOVING_BILL]
    async with open_session() as db:
        await db.execute(
            text("UPDATE invoices SET party_id = NULL WHERE id = :id"),
            {"id": moving["invoice_id"]},
        )
        await db.commit()
    await service.evaluate_pending()
    orphan = (await client.get(f"/decisions/{moving['id']}")).json()
    assert orphan["evaluation"] == moving["evaluation"] + 1
    assert failing(orphan)["vault_account"] == "hold"


async def test_the_log_names_the_firm_each_entry_was_made_for(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()
    decisions = await by_bill(client)
    delivered, moving = decisions[sandbox.DELIVERED_BILL], decisions[sandbox.MOVING_BILL]
    await answer(client, delivered, "approve")
    before = (await client.get(f"/invoices/{delivered['invoice_id']}")).json()["party_name"]

    # The books correct the bill to the other subcontractor after the approval.
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE invoices SET (party_id, party_name) = "
                "(SELECT party_id, party_name FROM invoices WHERE id = :other) WHERE id = :bill"
            ),
            {"other": moving["invoice_id"], "bill": delivered["invoice_id"]},
        )
        await db.commit()
    await service.evaluate_pending()

    log = (
        await client.get(
            "/decision-events",
            params={"decision_id": delivered["id"], "expand": "invoice"},
        )
    ).json()["items"]
    # The bill reads as the books hold it now; each entry keeps the firm it was made for.
    now = log[0]["invoice"]["party_name"]
    assert now != before and all(event["invoice"]["party_name"] == now for event in log)
    assert [(event["event"], event["party_name"]) for event in log] == [
        ("proposed", before),
        ("approved", before),
    ]


async def test_a_bill_the_firms_account_shows_paid_is_held_and_read_by_the_setting(
    client: httpx.AsyncClient, vault: Vault
) -> None:
    await a_sandbox(client)
    await service.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    # Paid on the firm's account in the books, the bill left open (ADR-0022).
    async with open_session() as db:
        await db.execute(
            text("UPDATE parties SET balance = 0 WHERE id = :party"),
            {"party": delivered["party_id"]},
        )
        await db.commit()

    await service.evaluate_pending()

    # Recorded on account, the books show it paid: nothing waits on anyone.
    paid = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert paid["state"] == "paid_in_books" and "on_account" in failing(paid)
    bill = (await client.get(f"/invoices/{delivered['invoice_id']}")).json()
    assert (bill["settled"], bill["account_owed"]) == ("paid_on_account", "0.00")
    refused = await client.patch(
        f"/decisions/{paid['id']}", json={"action": "approve", "evaluation": paid["evaluation"]}
    )
    assert refused.status_code == 409

    # Recorded per invoice, the open invoice and the account disagree: held for the owner.
    await client.patch("/policy", json={"payments_recorded": "per_invoice"})
    await service.evaluate_pending()
    held = (await by_bill(client))[sandbox.DELIVERED_BILL]
    assert held["state"] == "held" and "on_account" in failing(held)


async def test_a_policy_change_keeps_what_it_does_not_name(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    whole = {"currency": "EUR", "approval_limit": "500.00", "pay_when_paid": False}
    assert (await client.put("/policy", json=whole)).status_code == 200

    changed = await client.patch("/policy", json={"payments_recorded": "per_invoice"})

    assert changed.status_code == 200, changed.text
    policy = changed.json()
    assert (policy["version"], policy["payments_recorded"]) == (2, "per_invoice")
    assert (policy["currency"], policy["approval_limit"], policy["pay_when_paid"]) == (
        "EUR",
        "500.00",
        False,
    )


async def test_the_policy_names_how_the_company_pays_and_what_it_keeps(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    assert (await client.get("/policy")).json()["methods"] == ["vault_usdc"]

    # A bank only: crypto off.
    bank = await client.patch(
        "/policy",
        json={
            "methods": ["bank_transfer"],
            "default_method": "bank_transfer",
            "reserve": "25000.00",
            "payment_weekday": 3,
        },
    )
    assert bank.status_code == 200, bank.text
    policy = bank.json()
    assert (policy["methods"], policy["default_method"]) == (["bank_transfer"], "bank_transfer")
    assert (policy["reserve"], policy["payment_weekday"]) == ("25000.00", 3)

    # The default is one of the methods, each method once; with what the change keeps too.
    for wrong in (
        {"methods": ["vault_usdc"]},
        {"methods": ["bank_transfer", "bank_transfer"]},
    ):
        assert (await client.patch("/policy", json=wrong)).status_code == 400
    assert (await client.patch("/policy", json={"methods": []})).status_code == 422
    # Any day again; what it does not name stays.
    anyday = (await client.patch("/policy", json={"payment_weekday": -1})).json()
    assert anyday["payment_weekday"] is None and anyday["reserve"] == "25000.00"
