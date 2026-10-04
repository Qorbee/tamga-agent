"""The executor (ADR-0017): the Circle Agent Wallet that may call PolicyVault's ``release`` and
``anchor``. It has no API, so it is driven through Circle's CLI, signed in for testnet with an
emailed code whose session lasts seven days.

A call waits until Circle reports the transaction final, and returns it with its hash. The
same idempotency key sent again is the same transaction, and the vault refuses a payment key it
already paid, so a call repeated after a timeout can never pay twice.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.config import get_settings

logger = logging.getLogger(__name__)

# The CLI waits up to two minutes for the transaction to be final; this leaves it room.
_TIMEOUT: Final = 180.0
_FAILED: Final = frozenset({"FAILED", "CANCELLED", "DENIED"})


class WalletError(Exception):
    """The call was not made, or its transaction failed; ``code`` is the CLI's, ``reverted``
    whether the chain refused it, ``ended`` whether the transaction reached an end (failed,
    cancelled, denied) that sending its key again only repeats."""

    def __init__(
        self, message: str, *, code: str, reverted: bool = False, ended: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reverted = reverted
        self.ended = ended or reverted


class SignedOut(WalletError):
    """The CLI's session has ended: someone must sign in again with the emailed code."""


@dataclass(frozen=True, slots=True)
class Executed:
    transaction_id: str
    tx_hash: str


async def execute(
    signature: str,
    parameters: Sequence[str | int],
    *,
    contract: str,
    idempotency_key: uuid.UUID,
) -> Executed:
    """Calls ``signature`` on ``contract`` from the Agent Wallet and waits for it to be final."""
    settings = get_settings()
    if settings.agent_wallet_address is None:
        raise WalletError("set AGENT_WALLET_ADDRESS: no executor is named", code="NOT_CONFIGURED")
    answer = await _run(
        "wallet",
        "execute",
        signature,
        *[str(value) for value in parameters],
        "--contract",
        contract,
        "--address",
        settings.agent_wallet_address,
        "--chain",
        settings.circle_blockchain,
        "--idempotency-key",
        str(idempotency_key),
        "--output",
        "json",
    )
    transaction = answer.get("data")
    if not isinstance(transaction, dict):
        raise WalletError("the CLI's answer holds no transaction", code="INTERNAL")
    state = str(transaction.get("state", ""))
    if state in _FAILED:
        reason = str(transaction.get("errorReason") or state)
        raise WalletError(reason, code=state, reverted="revert" in reason.lower(), ended=True)
    tx_hash = transaction.get("txHash")
    if not tx_hash:
        raise WalletError(
            f"the transaction is {state or 'unknown'} and has no hash", code="PENDING"
        )
    return Executed(transaction_id=str(transaction.get("id", "")), tx_hash=str(tx_hash))


async def _run(*arguments: str) -> dict[str, Any]:
    command = get_settings().circle_cli
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise WalletError(
            f"Circle's CLI cannot be run: {type(error).__name__}", code="NO_CLI"
        ) from error
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=_TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise WalletError("Circle's CLI did not answer in time", code="TIMEOUT") from None
    try:
        answer = json.loads(out or b"{}")
    except ValueError:
        answer = {}
    if not isinstance(answer, dict):
        answer = {}
    refused = answer.get("error")
    if process.returncode != 0 or isinstance(refused, dict):
        details: dict[str, Any] = refused if isinstance(refused, dict) else {}
        code = str(details.get("code") or "INTERNAL")
        message = str(details.get("message") or err.decode(errors="replace").strip()[:300])
        if code == "AUTH_REQUIRED":
            raise SignedOut(message, code=code)
        logger.warning("Circle's CLI failed: %s %.300s", code, message)
        raise WalletError(message, code=code, reverted="revert" in message.lower())
    return answer
