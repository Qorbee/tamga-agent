import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.companies.models import Company
from app.modules.identity import service as identity
from app.modules.playground import service as playground
from app.modules.settlement import service as settlement
from app.modules.settlement.models import AccountState, PayeeWallet, VaultAccount
from app.platform import agent_wallet, chain, circle
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

VAULT = "0x6354dbccc4f5063fd10b9b1e4ff66d65d4224d0a"
USDC = "0x3600000000000000000000000000000000000000"
TREASURY = "0x42f1180241d22e93dfec51f68e8a59fe8752623a"


class FakeCircle:
    """Circle and the vault as an account sees them. Every call is written down; a transaction is
    final once it has been asked about as often as ``pending`` says for its function."""

    def __init__(self) -> None:
        self.made: list[tuple[list[str], uuid.UUID]] = []
        self.sent: list[tuple[str, list[Any], uuid.UUID]] = []
        self.pending: dict[str, int] = {}
        self.failing: set[str] = set()
        self.state: chain.CompanyState | None = None
        self.released: list[tuple[list[str | int], uuid.UUID]] = []
        self._wallets = 0

    async def create_wallets(
        self, names: Sequence[str], *, idempotency_key: uuid.UUID
    ) -> list[circle.Wallet]:
        self.made.append((list(names), idempotency_key))
        made = []
        for _ in names:
            self._wallets += 1
            made.append(circle.Wallet(id=f"w{self._wallets}", address=f"0x{self._wallets:040x}"))
        return made

    async def execute(
        self,
        *,
        wallet_id: str,
        contract: str,
        signature: str,
        parameters: Sequence[Any],
        idempotency_key: uuid.UUID,
    ) -> str:
        assert wallet_id == "treasury"
        self.sent.append((signature, list(parameters), idempotency_key))
        if signature.startswith("registerCompany"):
            _, owner, timelock, per_payment, daily, _ = parameters
            self.state = chain.CompanyState(owner, timelock, 0, 0, per_payment, daily, daily)
        if signature.startswith("deposit") and self.state is not None:
            self.state = replace(self.state, balance=self.state.balance + parameters[1])
        return f"tx-{len(self.sent)}"

    async def transaction(self, transaction_id: str) -> circle.Transaction:
        signature = self.sent[int(transaction_id.removeprefix("tx-")) - 1][0]
        name = signature.split("(")[0]
        if name in self.failing:
            return circle.Transaction(transaction_id, circle.State.FAILED, None, "reverted")
        if self.pending.get(name, 0) > 0:
            self.pending[name] -= 1
            return circle.Transaction(transaction_id, circle.State.PENDING, None, None)
        return circle.Transaction(transaction_id, circle.State.DONE, f"0xhash-{name}", None)

    async def wallet(self, wallet_id: str) -> circle.Wallet:
        return circle.Wallet(id=wallet_id, address=TREASURY)

    async def company_state(self, company_id: uuid.UUID) -> chain.CompanyState | None:
        return self.state

    async def usdc_balance(self, address: str) -> Decimal:
        return Decimal(100)

    async def release(
        self,
        signature: str,
        parameters: Sequence[str | int],
        *,
        contract: str,
        idempotency_key: uuid.UUID,
    ) -> agent_wallet.Executed:
        assert signature.startswith("release") and contract == VAULT
        self.released.append((list(parameters), idempotency_key))
        assert self.state is not None
        amount = int(parameters[3])
        self.state = replace(
            self.state,
            balance=self.state.balance - amount,
            left_today=self.state.left_today - amount,
        )
        return agent_wallet.Executed(transaction_id="agent-tx", tx_hash="0xreleased")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeCircle:
    """Accounts can be opened here, against a stand-in Circle and vault that answer at once."""
    settings = get_settings()
    monkeypatch.setattr(settings, "policy_vault_address", VAULT)
    monkeypatch.setattr(settings, "usdc_address", USDC)
    monkeypatch.setattr(settings, "circle_treasury_wallet_id", "treasury")
    monkeypatch.setattr(settings, "circle_api_key", SecretStr("TEST_API_KEY:x:y"))
    monkeypatch.setattr(settings, "circle_entity_secret", SecretStr("00" * 32))
    monkeypatch.setattr(settings, "circle_wallet_set_id", "set")
    stand_in = FakeCircle()
    for name in ("create_wallets", "execute", "transaction", "wallet"):
        monkeypatch.setattr(circle, name, getattr(stand_in, name))
    monkeypatch.setattr(chain, "company_state", stand_in.company_state)
    monkeypatch.setattr(chain, "usdc_balance", stand_in.usdc_balance)
    monkeypatch.setattr(agent_wallet, "execute", stand_in.release)
    monkeypatch.setattr(settlement, "_POLL", 0.0)
    return stand_in


async def seeded(client: httpx.AsyncClient) -> uuid.UUID:
    company_id = await guest_client(client)
    await playground.seed(company_id)
    return company_id


async def account(company_id: uuid.UUID) -> VaultAccount:
    async with open_session() as db:
        found = await db.scalar(select(VaultAccount).where(VaultAccount.company_id == company_id))
    assert found is not None
    return found


async def test_a_sandbox_is_registered_and_funded_from_the_treasury_once(
    client: httpx.AsyncClient, fake: FakeCircle
) -> None:
    company_id = await seeded(client)
    # Seeding queued the opening, with the sandbox's terms.
    async with open_session() as db:
        queued = await db.scalar(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = "
                "'settlement.open_account' AND args->>'company_id' = :id"
            ),
            {"id": str(company_id)},
        )
    assert queued == 1
    terms = settlement.sandbox_terms()
    assert (await account(company_id)).state is AccountState.OPENING

    await settlement.open_account(company_id)

    # One request made the owner key and a wallet for each subcontractor the sandbox owes.
    [(names, _)] = fake.made
    assert names[0] == "owner key" and len(names) == 3
    async with open_session() as db:
        payees = list(await db.scalars(select(PayeeWallet.address)))
    assert len(payees) == 2
    assert [signature.split("(")[0] for signature, _, _ in fake.sent] == [
        "registerCompany",
        "approve",
        "deposit",
    ]
    _, registered, _ = fake.sent[0]
    key, owner, timelock, per_payment, daily, listed = registered
    assert key == "0x" + chain.company_key(company_id).hex()
    assert owner == "0x" + f"{1:040x}"
    assert (timelock, per_payment, daily) == (
        terms.timelock_seconds,
        terms.per_payment_cap,
        terms.daily_cap,
    )
    # The treasury is a payee of a sandbox, so what is left of it can go back.
    assert sorted(listed) == sorted([*payees, TREASURY])
    assert fake.sent[2][1] == [key, terms.deposit]
    opened = await account(company_id)
    assert opened.state is AccountState.OPEN and opened.opened_at is not None
    assert {
        step: record["hash"] for step, record in opened.transactions.items() if "hash" in record
    } == {
        "register": "0xhash-registerCompany",
        "approve": "0xhash-approve",
        "deposit": "0xhash-deposit",
    }

    # Opened, it is never opened again.
    await settlement.open_account(company_id)
    assert len(fake.sent) == 3 and len(fake.made) == 1


async def test_a_step_tried_again_is_the_same_transaction(
    client: httpx.AsyncClient, fake: FakeCircle, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await seeded(client)
    # The deposit is not final before the job gives up waiting.
    fake.pending["deposit"] = 10**9
    monkeypatch.setattr(settlement, "_WAIT", 0.05)

    with pytest.raises(TimeoutError):
        await settlement.open_account(company_id)

    waiting = await account(company_id)
    assert waiting.state is AccountState.OPENING
    record = waiting.transactions["deposit"]
    assert record["id"] == "tx-3" and "hash" not in record
    # Its key was written before it was sent.
    assert fake.sent[2][2] == uuid.UUID(record["key"])

    fake.pending["deposit"] = 0
    await settlement.open_account(company_id)

    # The job tried again waited on the same deposit and sent nothing more.
    assert len(fake.sent) == 3
    assert (await account(company_id)).state is AccountState.OPEN


async def test_a_step_the_chain_refuses_ends_the_opening(
    client: httpx.AsyncClient, fake: FakeCircle
) -> None:
    company_id = await seeded(client)
    fake.failing.add("registerCompany")

    with pytest.raises(settlement.SettlementError, match="register failed"):
        await settlement.open_account(company_id)

    failed = await account(company_id)
    assert failed.state is AccountState.FAILED and failed.failure is not None
    # A failed account is left as it is: nothing more is sent.
    await settlement.open_account(company_id)
    assert len(fake.sent) == 1


async def test_nothing_is_opened_where_the_vault_is_not_configured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "policy_vault_address", None)
    company_id = await seeded(client)
    async with open_session() as db:
        held = await db.scalar(select(VaultAccount.id).where(VaultAccount.company_id == company_id))
    assert held is None


async def test_a_sandboxs_balance_goes_back_to_the_treasury_within_its_caps(
    client: httpx.AsyncClient, fake: FakeCircle
) -> None:
    company_id = await seeded(client)
    await settlement.open_account(company_id)
    assert fake.state is not None
    deposit = fake.state.balance
    cap = fake.state.per_payment_cap
    assert deposit > cap  # the case that needs two pieces

    # One payment may move at most the per-payment cap: the rest waits for the next try.
    assert await settlement.release_remainder(company_id) is False
    assert await settlement.release_remainder(company_id) is True
    assert [parameters[2:] for parameters, _ in fake.released] == [
        [TREASURY, cap],
        [TREASURY, deposit - cap],
    ]
    # Each piece is its own payment on the vault.
    assert fake.released[0][0][1] != fake.released[1][0][1]
    # Nothing is left: nothing more is sent.
    assert await settlement.release_remainder(company_id) is True
    assert len(fake.released) == 2


async def test_a_balance_left_for_another_day_keeps_the_sandbox(
    client: httpx.AsyncClient, fake: FakeCircle, db: AsyncSession
) -> None:
    company_id = await seeded(client)
    await settlement.open_account(company_id)
    assert fake.state is not None
    # Today's cap is spent: nothing can go back before tomorrow.
    fake.state = replace(fake.state, left_today=0)
    sandbox = await db.get(Company, company_id)
    assert sandbox is not None
    sandbox.expires_at = utcnow() - timedelta(minutes=1)
    await db.commit()

    assert await identity.expire_sandboxes() == 0
    assert fake.released == []
    assert await db.get(Company, company_id) is not None

    # The next day it goes back, and the sandbox with it.
    fake.state = replace(fake.state, left_today=fake.state.daily_cap)
    assert await identity.expire_sandboxes() == 0  # the first piece
    assert await identity.expire_sandboxes() == 1
    db.expunge_all()
    assert await db.get(Company, company_id) is None
    assert (
        sum(int(parameters[3]) for parameters, _ in fake.released)
        == settlement.sandbox_terms().deposit
    )


async def test_an_opening_that_never_said_it_finished_is_read_on_the_chain(
    client: httpx.AsyncClient, fake: FakeCircle, db: AsyncSession
) -> None:
    company_id = await seeded(client)
    await settlement.open_account(company_id)
    # The deposit landed, but the job died before it wrote the account open.
    opened = await db.scalar(select(VaultAccount).where(VaultAccount.company_id == company_id))
    assert opened is not None
    opened.state = AccountState.OPENING
    await db.commit()

    # Just begun, a step may still be on its way: the sandbox is kept.
    assert await settlement.release_remainder(company_id) is False
    assert fake.released == []

    # An hour on, the chain says what it holds, and that goes back first.
    opened.created_at = utcnow() - timedelta(hours=2)
    await db.commit()
    assert await settlement.release_remainder(company_id) is False
    assert await settlement.release_remainder(company_id) is True
    assert (
        sum(int(parameters[3]) for parameters, _ in fake.released)
        == settlement.sandbox_terms().deposit
    )


async def test_an_open_account_is_topped_up_once_however_often_a_stopped_run_is_tried(
    client: httpx.AsyncClient, fake: FakeCircle, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await seeded(client)
    await settlement.open_account(company_id)
    opened = fake.state
    assert opened is not None

    # The deposit is sent, then the run stops before it is final, and is tried again.
    monkeypatch.setattr(settlement, "_WAIT", 0.0)
    with pytest.raises(TimeoutError):
        await settlement.top_up(company_id, 2_000_000)
    monkeypatch.setattr(settlement, "_WAIT", 300.0)
    tx_hash = await settlement.top_up(company_id, 2_000_000)

    deposits = [sent for sent in fake.sent if sent[0].startswith("deposit")]
    assert len(deposits) == 2  # the opening's, and one top-up
    assert deposits[-1][1][1] == 2_000_000
    assert tx_hash == "0xhash-deposit"
    assert fake.state is not None and fake.state.balance == opened.balance + 2_000_000

    # A later top-up is a new transaction, not the finished one again.
    await settlement.top_up(company_id, 1_000_000)
    assert len([sent for sent in fake.sent if sent[0].startswith("deposit")]) == 3
    assert {"top-up 1", "top-up 2"} <= set((await account(company_id)).transactions)
