"""Circle's developer-controlled wallets (ADR-0015, ADR-0016): the treasury that administers
PolicyVault and funds it, and the wallets Tamga holds for payees, which only receive. A call
here makes a transaction in Circle; whether it reached the chain is read back by its id.

Testnet only: settings refuse a live key (root ``AGENTS.md``, invariant 4). The entity secret is
encrypted for each request with Circle's key, as Circle accepts each ciphertext once.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import httpx
from circle.web3 import utils  # type: ignore[import-untyped]  # the SDK ships no type information

from app.config import get_settings

_API: Final = "https://api.circle.com/v1/w3s"
_TIMEOUT: Final = 30.0


class CircleError(Exception):
    """Circle refused a request or could not be reached."""


class State(StrEnum):
    """Where a transaction stands, in Circle's words, reduced to what a caller acts on."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


# Circle's own states, by what they mean here.
_STATES: Final = {
    "COMPLETE": State.DONE,
    "CONFIRMED": State.DONE,
    "FAILED": State.FAILED,
    "CANCELLED": State.FAILED,
    "DENIED": State.FAILED,
}


@dataclass(frozen=True, slots=True)
class Transaction:
    id: str
    state: State
    tx_hash: str | None
    # Circle's reason for a failure, such as a revert; never the request.
    error: str | None


@dataclass(frozen=True, slots=True)
class Wallet:
    id: str
    address: str


def configured() -> bool:
    settings = get_settings()
    return (
        settings.circle_api_key is not None
        and settings.circle_entity_secret is not None
        and settings.circle_wallet_set_id is not None
    )


async def execute(
    *,
    wallet_id: str,
    contract: str,
    signature: str,
    parameters: Sequence[str | int | list[str]],
    idempotency_key: uuid.UUID,
) -> str:
    """Sends a contract call from one of Tamga's wallets; returns Circle's transaction id. The
    same idempotency key sent again gives the same transaction, never a second one; Circle takes
    only a UUID as one."""
    created = await _post(
        "/developer/transactions/contractExecution",
        {
            "idempotencyKey": str(idempotency_key),
            "walletId": wallet_id,
            "contractAddress": contract,
            "abiFunctionSignature": signature,
            "abiParameters": [_parameter(value) for value in parameters],
            "feeLevel": "MEDIUM",
        },
    )
    return str(created["id"])


async def transaction(transaction_id: str) -> Transaction:
    found = (await _get(f"/transactions/{transaction_id}"))["transaction"]
    return Transaction(
        id=transaction_id,
        state=_STATES.get(str(found.get("state")), State.PENDING),
        tx_hash=found.get("txHash") or None,
        error=found.get("errorReason") or found.get("errorDetails") or None,
    )


async def create_wallets(names: Sequence[str], *, idempotency_key: uuid.UUID) -> list[Wallet]:
    """New wallets in Tamga's wallet set, one per name, in order: wallets that only receive."""
    settings = get_settings()
    created = await _post(
        "/developer/wallets",
        {
            "idempotencyKey": str(idempotency_key),
            "walletSetId": settings.circle_wallet_set_id,
            "blockchains": [settings.circle_blockchain],
            "count": len(names),
            "accountType": "EOA",
            "metadata": [{"name": name[:50]} for name in names],
        },
    )
    return [
        Wallet(id=str(wallet["id"]), address=str(wallet["address"]))
        for wallet in created["wallets"]
    ]


async def wallet(wallet_id: str) -> Wallet:
    found = (await _get(f"/wallets/{wallet_id}"))["wallet"]
    return Wallet(id=str(found["id"]), address=str(found["address"]))


def _parameter(value: str | int | list[str]) -> Any:
    # Circle takes numbers as decimal strings, so a uint256 above 2^53 survives JSON.
    return str(value) if isinstance(value, int) else value


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    key, secret = _credentials()
    # The SDK fetches Circle's public key over blocking I/O the first time, so off the loop.
    ciphertext = await asyncio.to_thread(utils.generate_entity_secret_ciphertext, key, secret)
    return await _send("POST", path, key, {**body, "entitySecretCiphertext": ciphertext})


async def _get(path: str) -> dict[str, Any]:
    key, _ = _credentials()
    return await _send("GET", path, key, None)


async def _send(method: str, path: str, key: str, body: dict[str, Any] | None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=_API, headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT
        ) as client:
            response = await client.request(method, path, json=body)
    except httpx.HTTPError as error:
        raise CircleError(f"Circle could not be reached: {type(error).__name__}") from error
    if response.is_error:
        # Circle's answer names the field it refused; the request, which holds the ciphertext,
        # is never shown.
        raise CircleError(
            f"Circle refused {method} {path} ({response.status_code}): {response.text[:300]}"
        )
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise CircleError(f"Circle's answer to {method} {path} holds no data")
    return data


def _credentials() -> tuple[str, str]:
    settings = get_settings()
    if settings.circle_api_key is None or settings.circle_entity_secret is None:
        raise CircleError("set CIRCLE_API_KEY and CIRCLE_ENTITY_SECRET")
    return (
        settings.circle_api_key.get_secret_value(),
        settings.circle_entity_secret.get_secret_value(),
    )
