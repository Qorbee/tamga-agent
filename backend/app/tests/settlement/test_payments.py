"""The executor pays what the owner approved, and nothing else (ADR-0002): the evaluation's test
USDC from the vault to the subcontractor's wallet, under a key written before the call is sent,
settled only with the transaction's hash; refused by the vault, it fails or goes back to the
owner; a replay company records what it would have released (ADR-0009)."""

import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.modules.decisions import service as decisions
from app.modules.settlement import executor
from app.packs.logistics import sandbox
from app.platform import agent_wallet
from app.platform.db import open_session
from app.tests.decisions.test_decisions import Vault, a_sandbox, answer, by_bill
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

VAULT = "0x" + "a" * 40
EXECUTOR = "0x" + "e" * 40


class Executor:
    """The Agent Wallet as Circle's CLI would answer: each call kept, answered with ``answer``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer: agent_wallet.Executed | agent_wallet.WalletError = agent_wallet.Executed(
            transaction_id="circle-tx-1", tx_hash="0x" + "b" * 64
        )

    async def execute(
        self,
        signature: str,
        parameters: Sequence[str | int],
        *,
        contract: str,
        idempotency_key: uuid.UUID,
    ) -> agent_wallet.Executed:
        self.calls.append(
            {
                "signature": signature,
                "parameters": list(parameters),
                "contract": contract,
                "key": idempotency_key,
            }
        )
        if isinstance(self.answer, agent_wallet.WalletError):
            raise self.answer
        return self.answer


@pytest.fixture
def wallet(monkeypatch: pytest.MonkeyPatch) -> Executor:
    stand_in = Executor()
    monkeypatch.setattr(get_settings(), "policy_vault_address", VAULT)
    monkeypatch.setattr(get_settings(), "agent_wallet_address", EXECUTOR)
    monkeypatch.setattr(agent_wallet, "execute", stand_in.execute)
    return stand_in


async def approved(client: httpx.AsyncClient) -> dict[str, Any]:
    """A sandbox whose delivered truck's bill the owner approved."""
    await decisions.evaluate_pending()
    delivered = (await by_bill(client))[sandbox.DELIVERED_BILL]
    response = await answer(client, delivered, "approve")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_an_approved_bill_is_paid_once_and_settled_with_its_hash(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    company_id = await a_sandbox(client)
    decision = await approved(client)

    assert await executor.pay_approved() == 1

    [call] = wallet.calls
    assert call["signature"] == "release(bytes32,bytes32,address,uint256)"
    assert call["contract"] == VAULT
    company_key, key, payee, units = call["parameters"]
    assert company_key == "0x" + company_id.int.to_bytes(32, "big").hex()
    assert key == "0x" + decisions.payment_key(uuid.UUID(decision["invoice_id"])).hex()
    assert units == 220_400 and payee.startswith("0x")
    settled = (await client.get(f"/decisions/{decision['id']}")).json()
    assert settled["state"] == "settled"
    [payment] = (await client.get("/payments", params={"decision_id": decision["id"]})).json()[
        "items"
    ]
    assert payment["state"] == "settled" and payment["tx_hash"] == "0x" + "b" * 64
    assert payment["explorer_url"].endswith("/tx/0x" + "b" * 64)
    events = (await client.get("/decision-events", params={"decision_id": decision["id"]})).json()
    assert [event["event"] for event in events["items"]] == ["proposed", "approved", "settled"]
    assert events["items"][-1]["body"]["tx_hash"] == "0x" + "b" * 64
    # The log, newest first with its bills: the payment one tap from its transaction.
    log = (
        await client.get("/decision-events", params={"sort": "-seq", "expand": "invoice"})
    ).json()["items"]
    assert log[0]["event"] == "settled" and log[0]["explorer_url"] == payment["explorer_url"]
    assert {event["invoice"]["number"] for event in log} >= {sandbox.DELIVERED_BILL}
    assert all(event["explorer_url"] is None for event in log[1:])

    # Paid, it is never sent again, nor evaluated again.
    assert await executor.pay_approved() == 0
    assert await decisions.evaluate_pending() == 0
    assert len(wallet.calls) == 1
    async with open_session() as db:
        assert await decisions.broken_link(db, company_id) is None


async def test_a_call_that_did_not_finish_is_sent_again_as_the_same_transaction(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    await a_sandbox(client)
    decision = await approved(client)
    wallet.answer = agent_wallet.SignedOut("the session has ended", code="UNAUTHORIZED")

    assert await executor.pay_approved() == 0

    # Never marked paid without a hash: it stays approved, its key written down.
    assert (await client.get(f"/decisions/{decision['id']}")).json()["state"] == "approved"
    [payment] = (await client.get("/payments")).json()["items"]
    assert payment["state"] == "submitting" and payment["tx_hash"] is None
    wallet.answer = agent_wallet.Executed(transaction_id="circle-tx-1", tx_hash="0x" + "c" * 64)
    assert await executor.pay_approved() == 1
    first, second = wallet.calls
    assert first["key"] == second["key"]


async def test_a_cap_spent_since_the_evaluation_sends_the_bill_back_to_be_evaluated(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    await a_sandbox(client)
    decision = await approved(client)
    # Another payment spent most of today's cap after the approval.
    vault.state = dataclasses.replace(vault.state, left_today=100_000)
    wallet.answer = agent_wallet.WalletError(
        "execution reverted: OverDailyCap", code="FAILED", reverted=True
    )

    await executor.pay_approved()

    failed = (await client.get(f"/decisions/{decision['id']}")).json()
    assert failed["state"] == "failed" and failed["decided_by"] is None
    [payment] = (await client.get("/payments")).json()["items"]
    assert payment["state"] == "failed" and "OverDailyCap" in payment["failure"]
    # Evaluated again on the next pass, with what the chain says now: the rules see the cap
    # (the held bill beside it reads today's cap too, and moves with it).
    assert await decisions.evaluate_pending() == 2
    again = (await client.get(f"/decisions/{decision['id']}")).json()
    assert again["state"] == "held" and again["evaluation"] == 2
    assert any(r["rule"] == "vault_caps" and r["outcome"] == "hold" for r in again["rules"])
    events = (await client.get("/decision-events", params={"decision_id": decision["id"]})).json()
    assert [event["event"] for event in events["items"]] == [
        "proposed",
        "approved",
        "failed",
        "proposed",
    ]


async def test_a_key_the_vault_had_paid_fails_for_someone_to_look_at(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    await a_sandbox(client)
    decision = await approved(client)
    vault.paid.add(decisions.payment_key(uuid.UUID(decision["invoice_id"])))
    wallet.answer = agent_wallet.WalletError(
        "execution reverted: AlreadyPaid", code="FAILED", reverted=True
    )

    await executor.pay_approved()

    failed = (await client.get(f"/decisions/{decision['id']}")).json()
    assert failed["state"] == "failed" and failed["decided_by"] is not None
    assert await decisions.evaluate_pending() == 0
    # Never approved again as it was, and the same bill booked a second time is a duplicate.
    assert (await answer(client, failed, "approve")).status_code == 409
    moving = (await by_bill(client))[sandbox.MOVING_BILL]
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE invoices SET number = :number, party_id = "
                "(SELECT party_id FROM invoices WHERE id = :paid) WHERE id = :twin"
            ),
            {
                "number": sandbox.DELIVERED_BILL,
                "paid": decision["invoice_id"],
                "twin": moving["invoice_id"],
            },
        )
        await db.commit()
    await decisions.evaluate_pending()
    twin = (await client.get(f"/decisions/{moving['id']}")).json()
    assert any(r["rule"] == "duplicate" and r["outcome"] == "reject" for r in twin["rules"])


async def test_a_refusal_the_chain_does_not_explain_waits_for_someone_to_look_at_it(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    await a_sandbox(client)
    decision = await approved(client)
    wallet.answer = agent_wallet.WalletError(
        "execution reverted: NotExecutor", code="FAILED", reverted=True
    )

    await executor.pay_approved()

    # Evaluated again it would pass every rule and fail the same way: the approval is kept.
    failed = (await client.get(f"/decisions/{decision['id']}")).json()
    assert failed["state"] == "failed" and failed["decided_by"] is not None
    assert await decisions.evaluate_pending() == 0
    assert (await answer(client, failed, "approve")).status_code == 409
    assert await executor.pay_approved() == 0 and len(wallet.calls) == 1


async def test_a_replay_company_records_what_it_would_release_and_sends_nothing(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    company_id = await a_sandbox(client)
    decision = await approved(client)
    async with open_session() as db:
        await db.execute(
            text("UPDATE companies SET mode = 'replay', replay_cursor = now() WHERE id = :id"),
            {"id": company_id},
        )
        await db.commit()

    assert await executor.pay_approved() == 0
    assert await executor.pay_approved() == 0

    assert wallet.calls == []
    [payment] = (await client.get("/payments")).json()["items"]
    assert payment["state"] == "would_release" and payment["tx_hash"] is None
    events = (await client.get("/decision-events", params={"decision_id": decision["id"]})).json()
    assert [event["event"] for event in events["items"]] == [
        "proposed",
        "approved",
        "would_release",
    ]


async def test_nothing_is_sent_where_no_executor_is_named(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    await a_sandbox(client)
    await approved(client)
    monkeypatch.setattr(get_settings(), "agent_wallet_address", None)

    assert await executor.pay_approved() == 0

    assert wallet.calls == [] and (await client.get("/payments")).json()["items"] == []
