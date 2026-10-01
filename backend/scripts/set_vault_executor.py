"""Name PolicyVault's executor: the one wallet that may call `release` and `anchor` (ADR-0017).

uv run python scripts/set_vault_executor.py 0x<the executor's address>

The vault's admin, Tamga's treasury wallet, makes the call through Circle. It needs the Circle
keys, CIRCLE_TREASURY_WALLET_ID and POLICY_VAULT_ADDRESS in backend/.env, and test USDC in the
treasury wallet for gas. Run it once the Agent Wallet exists, and again to switch to the fallback
wallet or back: the vault has one executor at a time, and each switch is an `ExecutorSet` event on
the explorer. Naming the executor it already has changes nothing.
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import httpx

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from circle.web3 import utils  # noqa: E402
from pydantic import TypeAdapter, ValidationError  # noqa: E402

from app.config import EvmAddress, Settings, get_settings  # noqa: E402

CIRCLE_API = "https://api.circle.com/v1/w3s"
TIMEOUT_SECONDS = 300
# Circle's states for a transaction that will not change again.
_DONE = {"COMPLETE", "CONFIRMED"}
_FAILED = {"FAILED", "CANCELLED", "DENIED"}


def set_executor(settings: Settings, executor: str) -> str:
    """Sends `setExecutor(executor)` from the treasury wallet and returns the transaction's hash."""
    if settings.circle_api_key is None or settings.circle_entity_secret is None:
        raise SystemExit("Set CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET in backend/.env first")
    if settings.circle_treasury_wallet_id is None or settings.policy_vault_address is None:
        raise SystemExit("Set CIRCLE_TREASURY_WALLET_ID and POLICY_VAULT_ADDRESS first")
    # Settings already refused a key that is not a testnet key.
    api_key = settings.circle_api_key.get_secret_value()
    entity_secret = settings.circle_entity_secret.get_secret_value()
    request = {
        # A new key each run: sending the same call twice only sets the same executor again.
        "idempotencyKey": str(uuid.uuid4()),
        "walletId": settings.circle_treasury_wallet_id,
        "contractAddress": settings.policy_vault_address,
        "abiFunctionSignature": "setExecutor(address)",
        "abiParameters": [executor],
        "feeLevel": "MEDIUM",
        # Circle accepts each ciphertext once, so it is made per request.
        "entitySecretCiphertext": utils.generate_entity_secret_ciphertext(api_key, entity_secret),
    }
    with httpx.Client(
        base_url=CIRCLE_API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
    ) as circle:
        response = circle.post("/developer/transactions/contractExecution", json=request)
        if response.is_error:
            # Circle's error names the field; the request, which holds the ciphertext, is not shown.
            raise SystemExit(f"Circle refused the call ({response.status_code}): {response.text}")
        transaction_id = response.json()["data"]["id"]

        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            found = circle.get(f"/transactions/{transaction_id}").raise_for_status().json()
            transaction = found["data"]["transaction"]
            if transaction["state"] in _DONE:
                return str(transaction["txHash"])
            if transaction["state"] in _FAILED:
                raise SystemExit(
                    f"The call failed ({transaction['state']}): transaction {transaction_id} "
                    "in Circle's console"
                )
            time.sleep(3)
    raise SystemExit(f"No result after {TIMEOUT_SECONDS} s: transaction {transaction_id} in Circle")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executor", help="the executor wallet's address on the vault's chain")
    args = parser.parse_args()
    try:
        executor = TypeAdapter(EvmAddress).validate_python(args.executor)
    except ValidationError:
        raise SystemExit(f"{args.executor!r} is not an address") from None

    settings = get_settings()
    tx_hash = set_executor(settings, executor)
    explorer = settings.arc_explorer_url.rstrip("/")
    print(f"executor of {settings.policy_vault_address}: {executor}")
    print(f"# {explorer}/tx/{tx_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
