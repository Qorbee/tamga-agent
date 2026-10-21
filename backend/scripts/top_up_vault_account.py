"""Deposit more test USDC from the treasury into a company's open account on the vault.

uv run python scripts/top_up_vault_account.py COMPANY_ID --amount 30          # changes nothing
uv run python scripts/top_up_vault_account.py COMPANY_ID --amount 30 --write

For a company of the owner's whose account runs low, such as a pilot whose bank payments are
mirrored (ADR-0027). A sandbox's deposit is fixed and goes back to the treasury when it expires.
The deposit is written down before it is sent: a run that stops is run again and goes on with
the same transaction, never a second.
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
from app.modules.companies import service as companies  # noqa: E402
from app.modules.companies.models import CompanyKind  # noqa: E402
from app.modules.settlement import repository  # noqa: E402
from app.modules.settlement import service as settlement  # noqa: E402
from app.modules.settlement.models import AccountState  # noqa: E402
from app.packs import register_all  # noqa: E402
from app.platform import chain, circle  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def run(company_id: uuid.UUID, amount: Decimal, *, write: bool) -> int:
    settings = get_settings()
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        account = await repository.get_account(db, company_id)
    if company is None:
        print("No such company.")
        return 1
    print(f"{company.name} ({company.kind.value}, {company.mode.value})")
    if company.kind is CompanyKind.SANDBOX:
        print("A sandbox's deposit is fixed: it goes back to the treasury when the sandbox ends.")
        return 1
    if account is None or account.state is not AccountState.OPEN:
        print("It has no open account on the vault: open one with open_vault_account.py.")
        return 1
    if settings.circle_treasury_wallet_id is None:
        print("The treasury is not set here.")
        return 1
    try:
        state = await chain.company_state(company_id)
        treasury = await circle.wallet(settings.circle_treasury_wallet_id)
        held = await chain.usdc_balance(treasury.address)
    except (chain.ChainError, circle.CircleError) as error:
        print(f"The chain or Circle could not be read: {error}")
        return 1
    balance = chain.usdc(state.balance) if state is not None else Decimal(0)
    print(f"On the vault now: {balance} test USDC. The treasury holds {held}.")
    if amount > held:
        print(f"The treasury cannot give {amount}: top it up from the faucet first.")
        return 1
    print(f"A deposit of {amount} would make it {balance + amount}.")
    if not write:
        print("Nothing changed: run again with --write to deposit.")
        return 0
    try:
        tx_hash = await settlement.top_up(company_id, settlement.units(amount))
    except (settlement.SettlementError, TimeoutError) as error:
        print(f"Stopped: {error}. Run it again to go on where it stopped.")
        return 1
    print(f"Deposited: {settings.arc_explorer_url.rstrip('/')}/tx/{tx_hash}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("company_id", type=uuid.UUID)
    parser.add_argument("--amount", type=Decimal, required=True, help="test USDC")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.amount <= 0:
        parser.error("--amount is more than 0")
    import_all_models()
    register_all()
    init_db(str(get_settings().database_url))

    async def go() -> int:
        try:
            return await run(args.company_id, args.amount, write=args.write)
        finally:
            await dispose_db()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
