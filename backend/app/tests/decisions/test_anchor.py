"""The decision log's anchor (05, The log; ADR-0015): every company's head put on the chain as one
root, once it moved; a replay company's left out (ADR-0009); and rows removed from the end of a
log, which leave a chain that still holds, caught against the anchored head."""

import hashlib
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.modules.decisions import anchoring
from app.modules.decisions import service as decisions
from app.platform import agent_wallet, chain
from app.platform.db import open_session
from app.tests.decisions.test_decisions import Vault, a_sandbox
from app.tests.decisions.test_decisions import vault as vault  # noqa: F401  (the fixture)
from app.tests.settlement.test_payments import VAULT, Executor
from app.tests.settlement.test_payments import wallet as wallet  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket", "arc")]


@pytest.fixture
def arc(monkeypatch: pytest.MonkeyPatch, wallet: Executor) -> None:  # noqa: F811
    """The vault's record of each anchor: the root of the last anchor the executor sent."""

    async def anchored_root(tx_hash: str) -> str | None:
        sent = [call for call in wallet.calls if call["signature"] == "anchor(bytes32)"]
        return str(sent[-1]["parameters"][0])[2:] if sent else None

    monkeypatch.setattr(chain, "anchored_root", anchored_root)
    monkeypatch.setattr(decisions, "_ROOTS_ON_ARC", {})


def leaf(company: str, seq: int, hash_: str) -> bytes:
    return hashlib.sha256(
        chain.company_key(uuid.UUID(company)) + seq.to_bytes(32, "big") + bytes.fromhex(hash_)
    ).digest()


def test_the_root_pairs_the_leaves_in_the_companies_order_and_carries_an_odd_one_up() -> None:
    a, b, c = (str(uuid.UUID(int=n)) for n in (1, 2, 3))
    heads = {c: {"seq": 3, "hash": "cc" * 32}, a: {"seq": 1, "hash": "aa" * 32}}
    assert anchoring.root_of({a: heads[a]}) == leaf(a, 1, "aa" * 32).hex()
    pair = hashlib.sha256(leaf(a, 1, "aa" * 32) + leaf(c, 3, "cc" * 32)).digest()
    assert anchoring.root_of(heads) == pair.hex()
    three = {**heads, b: {"seq": 2, "hash": "bb" * 32}}
    left = hashlib.sha256(leaf(a, 1, "aa" * 32) + leaf(b, 2, "bb" * 32)).digest()
    assert anchoring.root_of(three) == hashlib.sha256(left + leaf(c, 3, "cc" * 32)).hexdigest()


async def test_the_heads_are_anchored_once_they_moved_and_a_shortened_log_is_caught(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    company_id = await a_sandbox(client)
    await decisions.evaluate_pending()
    wallet.answer = agent_wallet.Executed(transaction_id="circle-anchor", tx_hash="0x" + "d" * 64)

    assert await anchoring.anchor_heads() is True

    [call] = wallet.calls
    assert call["signature"] == "anchor(bytes32)" and call["contract"] == VAULT
    held = (await client.get("/decision-chain")).json()
    assert held["anchor"]["seq"] == held["length"] and held["anchor"]["hash"] == held["head"]
    assert held["anchor"]["holds"] and held["anchor"]["explorer_url"].endswith("d" * 64)
    assert held["anchor"]["on_arc"] is True
    async with open_session() as db:
        [root] = (await db.execute(text("SELECT root FROM decision_anchors"))).scalars()
    assert call["parameters"] == ["0x" + root]
    # Nothing moved: nothing is sent.
    assert await anchoring.anchor_heads() is False and len(wallet.calls) == 1

    # The newest row removed: what is left still holds, and only the anchor shows it gone.
    async with open_session() as db:
        await db.execute(
            text(
                "DELETE FROM decision_log WHERE company_id = :id AND seq = "
                "(SELECT max(seq) FROM decision_log WHERE company_id = :id)"
            ),
            {"id": company_id},
        )
        await db.commit()
    shortened = (await client.get("/decision-chain")).json()
    assert shortened["broken_at"] is None and shortened["anchor"]["holds"] is False
    # The next batch does not seal over the loss: the lost head is kept, and nothing is sent.
    assert await anchoring.anchor_heads() is False and len(wallet.calls) == 1
    assert (await client.get("/decision-chain")).json()["anchor"]["holds"] is False

    # The anchor kept here rewritten to match: its root is no longer the one on Arc.
    async with open_session() as db:
        await db.execute(
            text(
                "UPDATE decision_anchors SET heads = "
                "jsonb_set(heads, ARRAY[CAST(:id AS text), 'seq'], to_jsonb(CAST(:seq AS integer)))"
            ),
            {"id": str(company_id), "seq": shortened["length"]},
        )
        await db.commit()
    assert (await client.get("/decision-chain")).json()["anchor"]["on_arc"] is False


async def test_a_replay_company_is_left_out_and_a_refused_anchor_is_made_again(
    client: httpx.AsyncClient, vault: Vault, wallet: Executor
) -> None:
    company_id = await a_sandbox(client)
    await decisions.evaluate_pending()
    async with open_session() as db:
        await db.execute(
            text("UPDATE companies SET mode = 'replay', replay_cursor = now() WHERE id = :id"),
            {"id": company_id},
        )
        await db.commit()
    assert await anchoring.anchor_heads() is False and wallet.calls == []

    async with open_session() as db:
        await db.execute(
            text("UPDATE companies SET mode = 'shadow', replay_cursor = NULL WHERE id = :id"),
            {"id": company_id},
        )
        await db.commit()
    # Not final: sent again under the same key. Ended by Circle: the next batch makes a new one.
    wallet.answer = agent_wallet.SignedOut("the session has ended", code="UNAUTHORIZED")
    assert await anchoring.anchor_heads() is False
    wallet.answer = agent_wallet.WalletError("CANCELLED", code="CANCELLED", ended=True)
    assert await anchoring.anchor_heads() is False
    first, second = wallet.calls
    assert first["key"] == second["key"]
    wallet.answer = agent_wallet.Executed(transaction_id="circle-anchor", tx_hash="0x" + "e" * 64)
    assert await anchoring.anchor_heads() is True
    assert wallet.calls[-1]["key"] != first["key"]
    assert (await client.get("/decision-chain")).json()["anchor"]["holds"] is True
