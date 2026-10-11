"""The decision log's anchor (05, The log; ADR-0015): every company's head put on the chain as one
root, by the executor's `PolicyVault.anchor`, so rows removed from the end of a log, which leave a
shorter chain that still holds, are caught against what the chain says.

A replay company's heads are left out: its decisions never happened (ADR-0009). The anchor keeps
every head it covers, so a company's log is checked against its own head, and the root can be
computed again by anyone holding the heads. Its idempotency key is written before the call is
sent, as a payment's is, so a call sent again is the same transaction.
"""

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Any, Final

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.decisions import repository
from app.modules.decisions.models import AnchorState, DecisionAnchor
from app.modules.settlement import service as settlement
from app.platform import agent_wallet, chain, events
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

_ANCHOR: Final = "anchor(bytes32)"
# How often the heads are anchored when they moved; a payment settled since the last anchor is
# anchored within the minute, so its row is sealed on the chain while someone looks at it.
ANCHOR_EVERY: Final = timedelta(minutes=15)


async def anchor_heads() -> bool:
    """Puts the companies' heads on the chain when any moved since the last anchor, or sends an
    unfinished one again. Returns whether an anchor was settled. Where no vault or executor is
    configured, nothing is sent."""
    settings = get_settings()
    vault = settings.policy_vault_address
    if vault is None or settings.agent_wallet_address is None:
        return False
    async with open_session() as db:
        anchor = await repository.unfinished_anchor(db)
        if anchor is None:
            heads = await _heads()
            last = await repository.last_anchor(db)
            if last is not None:
                heads = await _kept(heads, last.heads)
            if not heads or (last is not None and last.heads == heads):
                return False
            if (
                last is not None
                and utcnow() - last.created_at < ANCHOR_EVERY
                and not await settlement.settled_since(db, last.created_at)
            ):
                return False
            anchor = DecisionAnchor(
                root=root_of(heads),
                heads=heads,
                state=AnchorState.SUBMITTING,
                idempotency_key=uuid.uuid4(),
            )
            db.add(anchor)
            await db.commit()
        anchor_id, root, key = anchor.id, anchor.root, anchor.idempotency_key

    try:
        done = await agent_wallet.execute(
            _ANCHOR, ["0x" + root], contract=vault, idempotency_key=key
        )
    except agent_wallet.WalletError as error:
        if not error.ended:
            # Signed out, not final yet, or the CLI not reached: sent again with the same key.
            logger.warning("anchoring %s waits: %s", anchor_id, error.code)
            return False
        async with open_session() as db:
            refused = await repository.get_anchor(db, anchor_id)
            if refused is not None:
                refused.state, refused.failure = AnchorState.FAILED, str(error)[:300]
                refused.finished_at = utcnow()
                await db.commit()
        return False

    async with open_session() as db:
        settled = await repository.get_anchor(db, anchor_id)
        if settled is not None:
            settled.state = AnchorState.SETTLED
            settled.tx_hash, settled.circle_transaction_id = done.tx_hash, done.transaction_id
            settled.finished_at = utcnow()
            # An open Log page says how far its log is sealed without waiting for a reload.
            for company in settled.heads:
                await events.publish(db, uuid.UUID(company), events.EventKind.DECISIONS)
            await db.commit()
    return True


async def _heads() -> dict[str, dict[str, Any]]:
    async with open_session() as db:
        found = await repository.heads(db)
        modes = await companies.get_companies(db, list(found))
    return {
        str(company_id): {"seq": seq, "hash": hash_}
        for company_id, (seq, hash_) in found.items()
        if company_id in modes and modes[company_id].mode is not CompanyMode.REPLAY
    }


async def _kept(
    heads: dict[str, dict[str, Any]], anchored: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The heads to anchor next. A company whose log no longer holds the head last anchored
    for it (rows removed from the end, or rewritten) keeps that head, so the newest anchor goes
    on showing the loss rather than sealing over it. A company deleted since, or in replay now,
    drops out."""
    before = {
        uuid.UUID(company): (int(head["seq"]), str(head["hash"]))
        for company, head in anchored.items()
    }
    async with open_session() as db:
        found = await repository.hashes_at(db, list(before.items()))
        alive = await companies.get_companies(db, list(before))
    kept = dict(heads)
    for company_id, (seq, hash_) in before.items():
        company = alive.get(company_id)
        if company is None or company.mode is CompanyMode.REPLAY:
            continue
        if found.get((company_id, seq)) != hash_:
            kept[str(company_id)] = {"seq": seq, "hash": hash_}
    return kept


def root_of(heads: dict[str, dict[str, Any]]) -> str:
    """A Merkle root over the heads in the order of the companies' keys. Each leaf is the SHA-256
    of a company's key, its head's place in the chain (32 bytes) and its hash; each pair of nodes
    is hashed together, an odd one carried up as it is. One company's head is then shown to be in
    a root by the nodes beside its path, without the other companies' heads."""
    level = [
        hashlib.sha256(
            chain.company_key(uuid.UUID(company))
            + int(head["seq"]).to_bytes(32, "big")
            + bytes.fromhex(head["hash"])
        ).digest()
        for company, head in sorted(heads.items(), key=lambda item: uuid.UUID(item[0]).int)
    ]
    while len(level) > 1:
        level = [
            hashlib.sha256(level[i] + level[i + 1]).digest() if i + 1 < len(level) else level[i]
            for i in range(0, len(level), 2)
        ]
    return level[0].hex()
