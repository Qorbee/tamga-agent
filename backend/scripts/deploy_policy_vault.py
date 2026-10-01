"""Deploy PolicyVault to Arc testnet through Circle's contracts API, once.

uv run python scripts/deploy_policy_vault.py

It needs the Circle keys and CIRCLE_TREASURY_WALLET_ID in backend/.env, test USDC in the treasury
wallet (on Arc, gas is USDC), and the contract built: `forge build` in contracts/. The treasury
wallet deploys the vault and becomes its admin. No executor is named: the admin does that with
`setExecutor` once the Agent Wallet exists (ADR-0017), and until then the vault pays nobody.

It prints POLICY_VAULT_ADDRESS to put in backend/.env. Run again with that set, it deploys nothing.
Run again before that, it asks Circle for the same deployment: the request's key is made from the
wallet, the bytecode and the constructor's arguments, so one build with one set of arguments is
never deployed twice, and a run with a corrected argument is a deployment of its own.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from circle.web3 import developer_controlled_wallets as dcw  # noqa: E402
from circle.web3 import utils  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402

CIRCLE_API = "https://api.circle.com/v1/w3s"
ARTIFACT = (
    Path(__file__).resolve().parent.parent.parent / "contracts/out/PolicyVault.sol/PolicyVault.json"
)
NO_EXECUTOR = "0x0000000000000000000000000000000000000000"
DEPLOY_TIMEOUT_SECONDS = 300


def artifact() -> tuple[str, str]:
    """The ABI and the creation bytecode `forge build` wrote."""
    if not ARTIFACT.exists():
        raise SystemExit(f"{ARTIFACT} is missing: run `forge build` in contracts/ first")
    built = json.loads(ARTIFACT.read_text())
    return json.dumps(built["abi"]), str(built["bytecode"]["object"])


def treasury_address(settings: Settings, api_key: str, entity_secret: str) -> str:
    client = utils.init_developer_controlled_wallets_client(
        api_key=api_key, entity_secret=entity_secret
    )
    wallet = dcw.WalletsApi(client).get_wallet(uuid.UUID(settings.circle_treasury_wallet_id))
    return str(wallet.data.wallet.actual_instance.address)


def deploy(settings: Settings, min_timelock: int) -> dict[str, Any]:
    if settings.circle_api_key is None or settings.circle_entity_secret is None:
        raise SystemExit("Set CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET in backend/.env first")
    if settings.circle_treasury_wallet_id is None:
        raise SystemExit(
            "Set CIRCLE_TREASURY_WALLET_ID: scripts/create_circle_wallets.py prints it"
        )
    # Settings already refused a key that is not a testnet key.
    api_key = settings.circle_api_key.get_secret_value()
    entity_secret = settings.circle_entity_secret.get_secret_value()
    abi, bytecode = artifact()
    admin = treasury_address(settings, api_key, entity_secret)

    constructor = [settings.usdc_address, admin, NO_EXECUTOR, min_timelock]
    identity = json.dumps([settings.circle_treasury_wallet_id, bytecode, constructor])
    request = {
        "idempotencyKey": str(uuid.uuid5(uuid.NAMESPACE_OID, identity)),
        "name": "PolicyVault",
        "description": "Pays each company's payees through the executor, within its caps",
        "walletId": settings.circle_treasury_wallet_id,
        "blockchain": settings.circle_blockchain,
        "abiJson": abi,
        "bytecode": bytecode,
        "constructorParameters": constructor,
        "feeLevel": "MEDIUM",
        # Circle accepts each ciphertext once, so it is made per request.
        "entitySecretCiphertext": utils.generate_entity_secret_ciphertext(api_key, entity_secret),
    }
    with httpx.Client(
        base_url=CIRCLE_API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
    ) as circle:
        response = circle.post("/contracts/deploy", json=request)
        if response.is_error:
            # Circle's error names the field; the request, which holds the ciphertext, is not shown.
            raise SystemExit(f"Circle refused the deploy ({response.status_code}): {response.text}")
        contract_id = response.json()["data"]["contractId"]

        deadline = time.monotonic() + DEPLOY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            contract = circle.get(f"/contracts/{contract_id}").raise_for_status().json()
            contract = contract["data"]["contract"]
            if contract["status"] == "COMPLETE":
                return {**contract, "admin": admin}
            if contract["status"] == "FAILED":
                raise SystemExit(
                    f"The deploy failed: see contract {contract_id} in Circle's console"
                )
            time.sleep(3)
    raise SystemExit(
        f"No result after {DEPLOY_TIMEOUT_SECONDS} s: contract {contract_id} in Circle"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-timelock",
        type=int,
        default=60,
        help="the shortest timelock a company may be registered with, in seconds; the contract "
        "refuses less than 60 (ADR-0016)",
    )
    args = parser.parse_args()

    settings = get_settings()
    explorer = settings.arc_explorer_url.rstrip("/")
    if settings.policy_vault_address:
        print(f"POLICY_VAULT_ADDRESS={settings.policy_vault_address}")
        print(f"# already deployed: {explorer}/address/{settings.policy_vault_address}")
        return 0

    contract = deploy(settings, args.min_timelock)
    print(f"POLICY_VAULT_ADDRESS={contract['contractAddress']}")
    print(f"# {explorer}/address/{contract['contractAddress']}")
    print(f"# admin (the treasury wallet): {contract['admin']}; no executor yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
