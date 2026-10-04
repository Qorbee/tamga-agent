"""Open a company's account on the vault, with the caps and timelock given here.

uv run python scripts/open_vault_account.py COMPANY_ID --deposit 5 --per-payment-cap 1 \\
    --daily-cap 3 --timelock 86400            # prints what it would register; changes nothing
uv run python scripts/open_vault_account.py COMPANY_ID ... --write

A sandbox is opened when it is seeded; this is for a company of the owner's (ADR-0016). With
--write it makes the company's owner key and a receive-only wallet for each party its books owe,
registers the company on the vault from the treasury, and deposits from the treasury, waiting for
each transaction to be final. Those parties are its payees from the start, active at once; a
party owed later needs a payee change signed by the owner key, which is not built yet. Each step
is written down before it is sent, so a run that stops is run again and goes on where it
stopped. A replay company is never put on the chain (ADR-0009).
"""

import argparse
import asyncio
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.modules.books import service as books  # noqa: E402
from app.modules.companies import service as companies  # noqa: E402
from app.modules.companies.models import CompanyKind, CompanyMode  # noqa: E402
from app.modules.parties import service as parties  # noqa: E402
from app.modules.settlement import repository  # noqa: E402
from app.modules.settlement import service as settlement  # noqa: E402
from app.modules.settlement.models import AccountState  # noqa: E402
from app.packs import register_all  # noqa: E402
from app.platform import chain  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def run(company_id: uuid.UUID, terms: settlement.Terms, *, write: bool) -> int:
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None:
            print("No such company.")
            return 1
        owed = await books.payable_parties(db, company_id)
        names = await parties.get_parties(db, company_id, owed)
        account = await repository.get_account(db, company_id)

    print(f"{company.name} ({company.kind.value}, {company.mode.value})")
    if company.kind is CompanyKind.SANDBOX:
        print("A sandbox is opened when it is seeded, with the sandbox terms.")
        return 1
    if company.mode is CompanyMode.REPLAY:
        print("A replay company is never put on the chain.")
        return 1
    if account is not None:
        print(f"It has an account already: {account.state.value}.")
        if account.state is AccountState.FAILED:
            print(f"It failed: {account.failure}")
            return 1
        if account.state is AccountState.OPEN:
            return _transactions(account.transactions)
        # Opening: a run that stopped goes on with the terms it wrote, never others.
        stored = settlement.Terms(
            deposit=account.deposit,
            per_payment_cap=account.per_payment_cap,
            daily_cap=account.daily_cap,
            timelock_seconds=account.timelock_seconds,
        )
        _show(stored)
        if stored != terms:
            print("These are the terms its opening began with: run again with them.")
            return 1
    else:
        _show(terms)
        try:
            shortest = await chain.min_timelock()
        except chain.ChainError as error:
            print(f"The vault could not be read: {error}")
            return 1
        if terms.timelock_seconds < shortest:
            print(f"The vault registers no timelock under {shortest} s.")
            return 1
    print(f"Its payees, the {len(owed)} parties its books owe:")
    for party in owed:
        print(f"  {names[party].name if party in names else party}")
    if not write:
        print("Nothing changed: run again with --write to open it.")
        return 0

    if account is None:
        async with open_session() as db:
            # Not queued for the worker: one opening at a time, run here to see it through.
            if not await settlement.write_account(db, company_id, terms):
                print("Accounts cannot be opened here: the vault, the treasury or Circle's keys")
                print("are not set.")
                return 1
            await db.commit()
    try:
        await settlement.open_account(company_id)
    except (settlement.SettlementError, TimeoutError) as error:
        print(f"Stopped: {error}. Run it again to go on where it stopped.")
        return 1
    async with open_session() as db:
        opened = await repository.get_account(db, company_id)
    if opened is None:
        return 1
    print(f"The account is {opened.state.value}.")
    return _transactions(opened.transactions)


def _transactions(transactions: dict[str, dict[str, str]]) -> int:
    explorer = get_settings().arc_explorer_url.rstrip("/")
    for name in ("register", "approve", "deposit"):
        if tx_hash := transactions.get(name, {}).get("hash"):
            print(f"  {name}: {explorer}/tx/{tx_hash}")
    return 0


def _show(terms: settlement.Terms) -> None:
    print(
        f"Deposit {_usdc(terms.deposit)}, at most {_usdc(terms.per_payment_cap)} a payment "
        f"and {_usdc(terms.daily_cap)} a day, a timelock of {terms.timelock_seconds} s."
    )


def _usdc(units: int) -> str:
    return f"{Decimal(units) / 10**6} test USDC"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("company_id", type=uuid.UUID)
    parser.add_argument("--deposit", type=Decimal, required=True, help="test USDC")
    parser.add_argument("--per-payment-cap", type=Decimal, required=True, help="test USDC")
    parser.add_argument("--daily-cap", type=Decimal, required=True, help="test USDC")
    parser.add_argument("--timelock", type=int, required=True, help="seconds, for later changes")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    terms = settlement.Terms(
        deposit=settlement.units(args.deposit),
        per_payment_cap=settlement.units(args.per_payment_cap),
        daily_cap=settlement.units(args.daily_cap),
        timelock_seconds=args.timelock,
    )
    if min(terms.deposit, terms.per_payment_cap, terms.daily_cap) <= 0:
        parser.error("the deposit and the caps are more than zero")
    if terms.per_payment_cap > terms.daily_cap:
        parser.error("a payment's cap is at most the day's")

    import_all_models()
    register_all()
    init_db(str(get_settings().database_url))

    async def go() -> int:
        try:
            return await run(args.company_id, terms, write=args.write)
        finally:
            await dispose_db()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
