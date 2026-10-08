import json
import stat
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.platform import agent_wallet, chain, circle

VAULT = "0x6354dbccc4f5063fd10b9b1e4ff66d65d4224d0a"
OWNER = "0x42f1180241d22e93dfec51f68e8a59fe8752623a"
PAYEE = "0x00000000000000000000000000000000000000aa"


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The vault's address in settings, and every request the node is sent."""
    monkeypatch.setattr(get_settings(), "policy_vault_address", VAULT)
    return []


def node(
    monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]], answer: dict[str, Any]
) -> None:
    async def post(request: dict[str, Any]) -> dict[str, Any]:
        sent.append(request)
        return answer

    monkeypatch.setattr(chain, "_post", post)


def words(*values: int | str) -> str:
    out = b""
    for value in values:
        if isinstance(value, str):
            out += bytes.fromhex(value.removeprefix("0x")).rjust(32, b"\0")
        else:
            out += value.to_bytes(32, "big")
    return "0x" + out.hex()


async def test_a_companys_state_is_read_from_the_vault(
    monkeypatch: pytest.MonkeyPatch, vault: list[dict[str, Any]]
) -> None:
    company_id = uuid.uuid4()
    state = words(OWNER, 86_400, 3, 25_000_000, 5_000_000, 20_000_000, 15_000_000, 0, 0, 0, 0, 0)
    node(monkeypatch, vault, {"jsonrpc": "2.0", "id": 1, "result": state})

    read = await chain.company_state(company_id)

    assert read == chain.CompanyState(
        owner=OWNER,
        timelock_seconds=86_400,
        nonce=3,
        balance=25_000_000,
        per_payment_cap=5_000_000,
        daily_cap=20_000_000,
        left_today=15_000_000,
    )
    assert chain.usdc(read.left_today) == 15
    # The call is the function's selector and the company's id as a 32-byte number.
    [call] = [request["params"][0] for request in vault]
    assert call["to"] == VAULT
    assert call["data"] == "0xe831f054" + company_id.int.to_bytes(32, "big").hex()


async def test_a_company_the_vault_does_not_know_has_no_state(
    monkeypatch: pytest.MonkeyPatch, vault: list[dict[str, Any]]
) -> None:
    node(monkeypatch, vault, {"error": {"code": 3, "message": "execution reverted"}})
    assert await chain.company_state(uuid.uuid4()) is None

    # Any other refusal is an error, which never names the node's URL.
    node(monkeypatch, vault, {"error": {"code": -32000, "message": "rate limited"}})
    with pytest.raises(chain.ChainError, match="refused") as raised:
        await chain.company_state(uuid.uuid4())
    assert "rpc" not in str(raised.value)


async def test_a_paid_key_and_a_payees_date_are_read(
    monkeypatch: pytest.MonkeyPatch, vault: list[dict[str, Any]]
) -> None:
    company_id = uuid.uuid4()
    key = chain.payment_key("bill", "final")
    node(monkeypatch, vault, {"result": words(1)})
    assert await chain.is_paid(company_id, key) is True
    assert vault[-1]["params"][0]["data"].endswith(key.hex())

    node(monkeypatch, vault, {"result": words(1_790_000_000)})
    assert await chain.payee_active_from(company_id, PAYEE) == 1_790_000_000
    node(monkeypatch, vault, {"result": words(0)})
    assert await chain.payee_active_from(company_id, PAYEE) is None
    # The same parts are the same key, so the vault refuses them a second time.
    assert chain.payment_key("bill", "final") == key != chain.payment_key("bill", "part")


async def test_a_node_that_fails_leaves_its_token_out_of_the_error_and_its_traceback(
    vault: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        get_settings(), "arc_rpc_url", SecretStr("https://rpc.example/v1/secret-token")
    )
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs)
    )

    with pytest.raises(chain.ChainError) as raised:
        await chain.company_state(uuid.uuid4())

    # A failed job is logged with its whole traceback, chained exceptions included.
    assert "secret-token" not in "".join(traceback.format_exception(raised.value))


async def test_nothing_is_read_without_a_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "policy_vault_address", None)
    with pytest.raises(chain.ChainError, match="POLICY_VAULT_ADDRESS"):
        await chain.company_state(uuid.uuid4())


def cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any], code: int = 0
) -> Path:
    """A stand-in for Circle's CLI that writes down its arguments and gives ``answer``."""
    seen = tmp_path / "arguments.json"
    script = tmp_path / "circle"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"print(json.dumps({answer!r}))\n"
        f"sys.exit({code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(get_settings(), "circle_cli", str(script))
    monkeypatch.setattr(get_settings(), "agent_wallet_address", OWNER)
    return seen


async def test_the_executor_calls_the_vault_through_the_cli_and_waits_for_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = cli(
        tmp_path, monkeypatch, {"data": {"id": "tx-1", "state": "COMPLETE", "txHash": "0xabc"}}
    )
    key = uuid.uuid4()

    done = await agent_wallet.execute(
        "release(bytes32,bytes32,address,uint256)",
        ["0x01", "0x02", PAYEE, 1_500_000],
        contract=VAULT,
        idempotency_key=key,
    )

    assert done == agent_wallet.Executed(transaction_id="tx-1", tx_hash="0xabc")
    arguments = json.loads(seen.read_text())
    assert arguments[:7] == [
        "wallet",
        "execute",
        "release(bytes32,bytes32,address,uint256)",
        "0x01",
        "0x02",
        PAYEE,
        "1500000",
    ]
    assert arguments[arguments.index("--idempotency-key") + 1] == str(key)
    assert arguments[arguments.index("--address") + 1] == OWNER
    assert arguments[arguments.index("--chain") + 1] == "ARC-TESTNET"


async def test_a_signed_out_cli_and_a_reverted_call_are_told_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli(tmp_path, monkeypatch, {"error": {"code": "AUTH_REQUIRED", "message": "Not logged in"}}, 1)
    with pytest.raises(agent_wallet.SignedOut):
        await agent_wallet.execute(
            "anchor(bytes32)", ["0x01"], contract=VAULT, idempotency_key=uuid.uuid4()
        )

    cli(
        tmp_path,
        monkeypatch,
        {
            "data": {
                "id": "tx-2",
                "state": "FAILED",
                "errorReason": "execution reverted: AlreadyPaid",
            }
        },
    )
    with pytest.raises(agent_wallet.WalletError) as failed:
        await agent_wallet.execute(
            "anchor(bytes32)", ["0x01"], contract=VAULT, idempotency_key=uuid.uuid4()
        )
    assert failed.value.reverted and failed.value.code == "FAILED"


async def test_circle_is_sent_numbers_as_text_and_its_states_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "circle_api_key", settings.circle_api_key or "TEST_API_KEY:x:y")
    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    async def send(method: str, path: str, key: str, body: dict[str, Any] | None) -> dict[str, Any]:
        sent.append((method, path, body))
        if method == "GET":
            return {"transaction": {"state": "COMPLETE", "txHash": "0xdef"}}
        return {"id": "circle-tx"}

    monkeypatch.setattr(circle, "_send", send)
    monkeypatch.setattr(circle, "_credentials", lambda: ("TEST_API_KEY:x:y", "00" * 32))
    monkeypatch.setattr(
        "app.platform.circle.utils.generate_entity_secret_ciphertext", lambda key, secret: "c"
    )

    key = uuid.uuid4()
    made = await circle.execute(
        wallet_id="treasury",
        contract=VAULT,
        signature="deposit(bytes32,uint256)",
        parameters=["0x01", 2**60],
        idempotency_key=key,
    )
    read = await circle.transaction(made)

    assert made == "circle-tx"
    [(_, path, body), _] = sent
    assert path == "/developer/transactions/contractExecution"
    assert body is not None and body["abiParameters"] == ["0x01", str(2**60)]
    assert body["idempotencyKey"] == str(key)
    assert body["entitySecretCiphertext"] == "c"
    assert read == circle.Transaction(
        id="circle-tx", state=circle.State.DONE, tx_hash="0xdef", error=None
    )
