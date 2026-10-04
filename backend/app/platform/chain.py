"""Reads PolicyVault on Arc (ADR-0015) over the node's JSON-RPC: a company's state on the vault,
whether a payment key was paid, and when a payee may be paid. It only reads; whatever the vault
is sent goes through a Circle wallet (``circle.py``, ``agent_wallet.py``).

The engine reads the caps here rather than keeping its own copy, so the rules and the contract
cannot disagree about them ([05](../../docs/architecture/05-decisions-and-settlement.md)). A call
takes its function's selector from the ABI's export, so no Keccak implementation is needed here.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Final

import httpx

from app.config import get_settings

_SELECTORS: Final = Path(__file__).parent / "abi" / "PolicyVault.selectors.json"
_WORD: Final = 32
# USDC's decimals on Arc's ERC-20 interface.
USDC_UNIT: Final = Decimal(10) ** 6
# A JSON-RPC error a node gives for a call the contract reverted.
_REVERTED: Final = 3
_TIMEOUT: Final = 15.0
# The topic of the vault's `Anchored(uint256,bytes32)` event: `cast keccak` of that signature.
_ANCHORED: Final = "0x2946f6d79e30b36612401c9600819a1e622cf156a41e314844f4d582f06d242a"


class ChainError(Exception):
    """The node could not be asked, or answered something that is no answer to the call."""


@dataclass(frozen=True, slots=True)
class CompanyState:
    """A company on the vault, in USDC's smallest units: its balance, the caps in force and what
    is left of today's (a UTC day), and the owner key that signs its changes."""

    owner: str
    timelock_seconds: int
    nonce: int
    balance: int
    per_payment_cap: int
    daily_cap: int
    left_today: int


def company_key(company_id: uuid.UUID) -> bytes:
    """A company as the vault keys it: its id as a 32-byte number. No name goes on-chain."""
    return company_id.int.to_bytes(_WORD, "big")


def payment_key(*parts: str) -> bytes:
    """A payment's key on the vault, from what makes it one payment (a bill and its milestone):
    the vault refuses a key it already paid, so the same parts can never be paid twice."""
    return hashlib.sha256("\x1f".join(parts).encode()).digest()


def usdc(units: int) -> Decimal:
    return Decimal(units) / USDC_UNIT


async def company_state(company_id: uuid.UUID) -> CompanyState | None:
    """The company's state on the vault; None while it is not registered there."""
    words = _words(await _call("companyState(bytes32)", company_key(company_id)))
    if words is None:
        return None
    if len(words) < 7:
        raise ChainError("companyState answered too few words")
    return CompanyState(
        owner=_address(words[0]),
        timelock_seconds=int.from_bytes(words[1]),
        nonce=int.from_bytes(words[2]),
        balance=int.from_bytes(words[3]),
        per_payment_cap=int.from_bytes(words[4]),
        daily_cap=int.from_bytes(words[5]),
        left_today=int.from_bytes(words[6]),
    )


async def is_paid(company_id: uuid.UUID, key: bytes) -> bool:
    words = _words(await _call("isPaid(bytes32,bytes32)", company_key(company_id), key))
    if not words:
        raise ChainError("isPaid reverted")
    return int.from_bytes(words[0]) != 0


async def payee_active_from(company_id: uuid.UUID, payee: str) -> int | None:
    """When the payee may first be paid, as a Unix time; None when it is not on the list."""
    words = _words(
        await _call("payeeActiveFrom(bytes32,address)", company_key(company_id), _word(payee))
    )
    if not words:
        raise ChainError("payeeActiveFrom reverted")
    active_from = int.from_bytes(words[0])
    return active_from or None


async def min_timelock() -> int:
    """The shortest timelock the vault registers a company with, in seconds."""
    words = _words(await _call("minTimelock()"))
    if not words:
        raise ChainError("minTimelock reverted")
    return int.from_bytes(words[0])


async def anchored_root(tx_hash: str) -> str | None:
    """The root the vault recorded in this transaction's ``Anchored`` event, as 64 hex digits;
    None when the transaction holds no such event, or is not known yet."""
    vault = get_settings().policy_vault_address
    if vault is None:
        raise ChainError("set POLICY_VAULT_ADDRESS: the vault is not deployed here")
    answer = await _post(
        {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash]}
    )
    if "error" in answer:
        raise ChainError(f"the node refused the call: {str(answer['error'])[:200]}")
    receipt = answer.get("result")
    if not isinstance(receipt, dict):
        return None
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        data = log.get("data")
        if (
            str(log.get("address", "")).lower() == vault.lower()
            and topics
            and str(topics[0]).lower() == _ANCHORED
            and isinstance(data, str)
            and len(data) == 2 + 2 * _WORD
        ):
            return data[2:].lower()
    return None


async def _call(signature: str, *arguments: bytes) -> bytes | None:
    """``eth_call`` on the vault; None when the contract reverted."""
    settings = get_settings()
    if settings.policy_vault_address is None:
        raise ChainError("set POLICY_VAULT_ADDRESS: the vault is not deployed here")
    data = _selectors()[signature] + b"".join(arguments)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": settings.policy_vault_address, "data": "0x" + data.hex()}, "latest"],
    }
    answer = await _post(request)
    if "error" in answer:
        error = answer["error"]
        if isinstance(error, dict) and error.get("code") == _REVERTED:
            return None
        # The node's message, not the request: the URL carries the team's token.
        raise ChainError(f"the node refused the call: {str(error)[:200]}")
    result = answer.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ChainError("the node's answer holds no result")
    return bytes.fromhex(result[2:])


async def _post(request: dict[str, Any]) -> dict[str, Any]:
    url = get_settings().arc_rpc_url.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            answer = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # The error's type only: its text can name the URL, and the URL holds a token.
        raise ChainError(f"the node could not be asked: {type(error).__name__}") from error
    if not isinstance(answer, dict):
        raise ChainError("the node's answer is not a JSON-RPC response")
    return answer


@cache
def _selectors() -> dict[str, bytes]:
    return {
        signature: bytes.fromhex(selector)
        for signature, selector in json.loads(_SELECTORS.read_text()).items()
    }


def _words(data: bytes | None) -> list[bytes] | None:
    if data is None:
        return None
    return [data[start : start + _WORD] for start in range(0, len(data), _WORD)]


def _word(address: str) -> bytes:
    return bytes.fromhex(address.removeprefix("0x")).rjust(_WORD, b"\0")


def _address(word: bytes) -> str:
    return "0x" + word[-20:].hex()
