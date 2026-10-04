"""Settlement (ADR-0002, ADR-0015, ADR-0016, ADR-0017): a company's account on PolicyVault, and
the one path by which test USDC leaves it.

Opening an account makes the company's owner key and a wallet for each party it owes, registers
the company on the vault with its caps, timelock and payees, and deposits its test USDC, all from
Tamga's treasury. Each step is a transaction whose idempotency key is written down before it is
sent, so a step tried again after a crash is the same transaction, never a second one. What the
vault holds is read from the chain, never kept here.

A sandbox gets a small account when it opens, with the treasury among its payees, and before it
is deleted what is left of its balance goes back to the treasury: the vault has no withdrawal,
and faucet USDC is scarce.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind, CompanyMode
from app.modules.settlement import repository
from app.modules.settlement.models import AccountState, PayeeWallet, VaultAccount
from app.platform import agent_wallet, chain, circle, jobs
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

# How often, and how long, a step waits for Circle to report its transaction final.
_POLL: Final = 3.0
_WAIT: Final = 300.0
# An opening unfinished this long after it began is taken as dead: its job is tried again within
# minutes, and Circle makes a transaction final in about one.
_ABANDONED: Final = 3600
# An allowance no treasury balance reaches.
_ANY_AMOUNT: Final = 2**128


class SettlementError(Exception):
    """A step of the account failed for a reason another try will not change."""


@dataclass(frozen=True, slots=True)
class Terms:
    """What a company is registered with, in USDC's smallest units."""

    deposit: int
    per_payment_cap: int
    daily_cap: int
    timelock_seconds: int


def units(amount: Decimal) -> int:
    return int(amount * chain.USDC_UNIT)


def sandbox_terms() -> Terms:
    settings = get_settings()
    return Terms(
        deposit=units(settings.sandbox_deposit_usdc),
        per_payment_cap=units(settings.sandbox_per_payment_cap_usdc),
        daily_cap=units(settings.sandbox_daily_cap_usdc),
        timelock_seconds=settings.sandbox_timelock_seconds,
    )


def configured() -> bool:
    """Whether accounts can be opened here: the vault, the treasury and Circle's keys are set."""
    settings = get_settings()
    return (
        settings.policy_vault_address is not None
        and settings.circle_treasury_wallet_id is not None
        and circle.configured()
    )


async def open_later(db: AsyncSession, company_id: uuid.UUID, terms: Terms) -> bool:
    """Writes the company's account with ``terms`` and queues its opening, in the caller's
    transaction. Nothing happens where accounts cannot be opened, or for a company that already
    has one. Returns whether an opening was queued."""
    from app.modules.settlement import tasks

    if not configured() or await repository.get_account(db, company_id) is not None:
        return False
    await repository.add(
        db,
        VaultAccount(
            company_id=company_id,
            state=AccountState.OPENING,
            timelock_seconds=terms.timelock_seconds,
            per_payment_cap=terms.per_payment_cap,
            daily_cap=terms.daily_cap,
            deposit=terms.deposit,
            transactions={},
        ),
    )
    await jobs.defer(db, tasks.open_account, args={"company_id": str(company_id)})
    return True


async def open_account(company_id: uuid.UUID) -> None:
    """Takes the company's account through every step it has not finished: wallets, the
    registration, the treasury's approval of the deposit, the deposit. A replay company never
    reaches the chain (ADR-0009)."""
    settings = get_settings()
    vault, treasury_id = settings.policy_vault_address, settings.circle_treasury_wallet_id
    if vault is None or treasury_id is None:
        raise SettlementError("the vault or the treasury is not configured")
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        account = await repository.get_account(db, company_id)
    if company is None or account is None or account.state is not AccountState.OPENING:
        return
    if company.mode is CompanyMode.REPLAY:
        raise SettlementError("a replay company is never put on the chain")
    try:
        owner, payees = await _wallets(company_id)
        treasury = await circle.wallet(treasury_id)
        key = chain.company_key(company_id)
        if await chain.company_state(company_id) is None:
            await _step(
                company_id,
                "register",
                lambda idempotency: circle.execute(
                    wallet_id=treasury_id,
                    contract=vault,
                    signature="registerCompany(bytes32,address,uint256,uint256,uint256,address[])",
                    parameters=[
                        "0x" + key.hex(),
                        owner,
                        account.timelock_seconds,
                        account.per_payment_cap,
                        account.daily_cap,
                        [*payees, *_treasury_for(company.kind, treasury.address)],
                    ],
                    idempotency_key=idempotency,
                ),
            )
        # The vault pulls a deposit only from whoever calls `deposit`, so letting it take any
        # amount from the treasury lets nobody else spend it; and openings that overlap do not
        # undo each other's allowance, as an exact one would.
        await _step(
            company_id,
            "approve",
            lambda idempotency: circle.execute(
                wallet_id=treasury_id,
                contract=settings.usdc_address,
                signature="approve(address,uint256)",
                parameters=[vault, _ANY_AMOUNT],
                idempotency_key=idempotency,
            ),
        )
        await _step(
            company_id,
            "deposit",
            lambda idempotency: circle.execute(
                wallet_id=treasury_id,
                contract=vault,
                signature="deposit(bytes32,uint256)",
                parameters=["0x" + key.hex(), account.deposit],
                idempotency_key=idempotency,
            ),
        )
    except SettlementError as error:
        await _fail(company_id, str(error))
        raise
    async with open_session() as db:
        opened = await repository.get_account(db, company_id, for_update=True)
        if opened is not None:
            opened.state = AccountState.OPEN
            opened.opened_at = utcnow()
            await db.commit()


async def release_remainder(company_id: uuid.UUID) -> bool:
    """Before a sandbox is deleted: sends what is left of its balance on the vault back to the
    treasury, through the executor. Returns whether nothing of it is left there, so the sandbox
    may go; False keeps it for the next try. The chain is read whatever the account's state:
    an opening that never said it finished may still have deposited."""
    async with open_session() as db:
        account = await repository.get_account(db, company_id)
    if account is None:
        return True
    if (
        account.state is AccountState.OPENING
        and time.time() - account.created_at.timestamp() < _ABANDONED
    ):
        # A deposit still on its way would land in a company nobody can pay from.
        return False
    settings = get_settings()
    vault, treasury_id = settings.policy_vault_address, settings.circle_treasury_wallet_id
    try:
        state = await chain.company_state(company_id)
        if state is None or state.balance == 0:
            return True
        # The vault holds this release to the sandbox's own caps, so a balance above what one
        # payment or the rest of today may move goes back in pieces, one each try.
        amount = min(state.balance, state.per_payment_cap, state.left_today)
        if amount == 0 or vault is None or treasury_id is None:
            return False
        treasury = await circle.wallet(treasury_id)
        await agent_wallet.execute(
            "release(bytes32,bytes32,address,uint256)",
            [
                "0x" + chain.company_key(company_id).hex(),
                # The balance it is taken from: each piece is its own payment, paid once.
                "0x" + chain.payment_key(str(company_id), "remainder", str(state.balance)).hex(),
                treasury.address,
                amount,
            ],
            contract=vault,
            # A new key each try; the payment key above is what the vault pays only once.
            idempotency_key=uuid.uuid4(),
        )
    except (chain.ChainError, circle.CircleError, agent_wallet.WalletError) as error:
        logger.warning("the remainder of %s stays on the vault for now: %s", company_id, error)
        return False
    return amount == state.balance


async def account_open(db: AsyncSession, company_id: uuid.UUID) -> bool:
    """Whether the company is registered on the vault and funded, so it can pay."""
    account = await repository.get_account(db, company_id)
    return account is not None and account.state is AccountState.OPEN


async def payee_addresses(
    db: AsyncSession, company_id: uuid.UUID, party_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The wallet Tamga holds for each of these parties, where it has one."""
    held = await repository.payee_wallets_of(db, company_id, party_ids)
    return {party_id: wallet.address for party_id, wallet in held.items()}


def _treasury_for(kind: CompanyKind, address: str) -> list[str]:
    # A sandbox's unspent balance returns to the treasury by a release, so it is a payee from the
    # start; a company's own payees are only the parties it owes.
    return [address] if kind is CompanyKind.SANDBOX else []


async def _wallets(company_id: uuid.UUID) -> tuple[str, list[str]]:
    """The company's owner key and a wallet per party it owes, made once: one request for all
    that are missing, under a key written down before it is sent."""
    async with open_session() as db:
        account = await repository.get_account(db, company_id, for_update=True)
        if account is None:
            raise SettlementError("the company has no account on the vault")
        owed = await books.payable_parties(db, company_id)
        held = await repository.payee_wallets_of(db, company_id, owed)
        missing = [party for party in owed if party not in held]
        if account.owner_address is not None and not missing:
            return account.owner_address, [held[party].address for party in owed]
        record = dict(account.transactions.get("wallets", {}))
        record.setdefault("key", str(uuid.uuid4()))
        account.transactions = {**account.transactions, "wallets": record}
        await db.commit()
    owner = [] if account.owner_address is not None else ["owner key"]
    made = await circle.create_wallets(
        [*owner, *(f"payee {party}" for party in missing)], idempotency_key=uuid.UUID(record["key"])
    )
    async with open_session() as db:
        account = await repository.get_account(db, company_id, for_update=True)
        if account is None:
            raise SettlementError("the company has no account on the vault")
        if owner:
            account.owner_wallet_id, account.owner_address = made[0].id, made[0].address
            made = made[1:]
        for party, wallet in zip(missing, made, strict=True):
            await repository.add(
                db,
                PayeeWallet(
                    company_id=company_id,
                    party_id=party,
                    circle_wallet_id=wallet.id,
                    address=wallet.address,
                ),
            )
        # The next request for wallets, should another party be owed, is a new one.
        account.transactions = {
            **account.transactions,
            "wallets": {"key": str(uuid.uuid4())},
        }
        await db.commit()
        owner_address = account.owner_address
        held = await repository.payee_wallets_of(db, company_id, owed)
    if owner_address is None:
        raise SettlementError("the company has no owner key")
    return owner_address, [held[party].address for party in owed]


async def _step(
    company_id: uuid.UUID, name: str, send: Callable[[uuid.UUID], Awaitable[str]]
) -> str:
    """One transaction of the opening, sent once whatever happens: its key is written before it
    is sent, its Circle id after, its hash once final. Returns the hash."""
    async with open_session() as db:
        account = await repository.get_account(db, company_id, for_update=True)
        if account is None:
            raise SettlementError("the company has no account on the vault")
        record: dict[str, Any] = dict(account.transactions.get(name, {}))
        if record.get("hash"):
            return str(record["hash"])
        if "key" not in record:
            record["key"] = str(uuid.uuid4())
            account.transactions = {**account.transactions, name: record}
            await db.commit()
    transaction_id = record.get("id")
    if transaction_id is None:
        transaction_id = await send(uuid.UUID(record["key"]))
        await _write(company_id, name, {**record, "id": transaction_id})
    done = await _wait(str(transaction_id), name)
    await _write(company_id, name, {**record, "id": transaction_id, "hash": done})
    return done


async def _wait(transaction_id: str, name: str) -> str:
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline:
        found = await circle.transaction(transaction_id)
        if found.state is circle.State.DONE and found.tx_hash:
            return found.tx_hash
        if found.state is circle.State.FAILED:
            raise SettlementError(f"{name} failed: {found.error or 'no reason given'}"[:300])
        await asyncio.sleep(_POLL)
    # Not final yet: the job is tried again, and waits on the same transaction.
    raise TimeoutError(f"{name} is not final after {_WAIT:.0f} s")


async def _write(company_id: uuid.UUID, name: str, record: dict[str, Any]) -> None:
    async with open_session() as db:
        account = await repository.get_account(db, company_id, for_update=True)
        if account is None:
            raise SettlementError("the company has no account on the vault")
        account.transactions = {**account.transactions, name: record}
        await db.commit()


async def _fail(company_id: uuid.UUID, reason: str) -> None:
    async with open_session() as db:
        account = await repository.get_account(db, company_id, for_update=True)
        if account is not None:
            account.state = AccountState.FAILED
            account.failure = reason[:300]
            await db.commit()
