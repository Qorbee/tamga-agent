"""Create Tamga's Circle wallet set and the company treasury wallet on Arc testnet, once.

uv run python scripts/create_circle_wallets.py

Developer-controlled wallets have no console flow, so this is the way in. It needs CIRCLE_API_KEY
(a testnet key) and CIRCLE_ENTITY_SECRET, the one registered in Circle's console, in backend/.env.
It prints the ids to put back there as CIRCLE_WALLET_SET_ID and CIRCLE_TREASURY_WALLET_ID, and the
treasury's address for the faucet. Run again with those set, it creates nothing and prints them.

Circle's SDK encrypts the entity secret for every request itself; the secret never leaves this
process in the clear and is never printed.
"""

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from circle.web3 import developer_controlled_wallets as dcw  # noqa: E402
from circle.web3 import utils  # noqa: E402

from app.config import get_settings  # noqa: E402


def client() -> Any:
    settings = get_settings()
    if settings.circle_api_key is None or settings.circle_entity_secret is None:
        raise SystemExit("Set CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET in backend/.env first")
    # Settings already refused a key that is not a testnet key.
    return utils.init_developer_controlled_wallets_client(
        api_key=settings.circle_api_key.get_secret_value(),
        entity_secret=settings.circle_entity_secret.get_secret_value(),
    )


def wallet_set(api: Any, name: str) -> str:
    settings = get_settings()
    if settings.circle_wallet_set_id:
        return settings.circle_wallet_set_id
    request = dcw.CreateWalletSetRequest.from_dict({"name": name})
    response = dcw.WalletSetsApi(api).create_wallet_set(request)
    return str(response.data.wallet_set.actual_instance.id)


def treasury(api: Any, wallet_set_id: str) -> tuple[str, str]:
    settings = get_settings()
    wallets = dcw.WalletsApi(api)
    if settings.circle_treasury_wallet_id:
        found = wallets.get_wallet(uuid.UUID(settings.circle_treasury_wallet_id)).data.wallet
        found = found.actual_instance
        return str(found.id), str(found.address)
    request = dcw.CreateWalletRequest.from_dict(
        {
            "walletSetId": wallet_set_id,
            "blockchains": [settings.circle_blockchain],
            "count": 1,
            "accountType": "EOA",
            "metadata": [{"name": "Tamga treasury", "refId": "treasury"}],
        }
    )
    [created] = wallets.create_wallet(request).data.wallets
    wallet = created.actual_instance
    return str(wallet.id), str(wallet.address)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Tamga", help="the wallet set's name in Circle")
    args = parser.parse_args()

    api = client()
    set_id = wallet_set(api, args.name)
    wallet_id, address = treasury(api, set_id)
    print(f"CIRCLE_WALLET_SET_ID={set_id}")
    print(f"CIRCLE_TREASURY_WALLET_ID={wallet_id}")
    print(f"# treasury address on {get_settings().circle_blockchain}: {address}")
    print("# fund it with test USDC from https://faucet.circle.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
