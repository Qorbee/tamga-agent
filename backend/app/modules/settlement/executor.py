"""The executor's payments (ADR-0002, ADR-0017): each decision the owner approved is released
from the company's vault to the subcontractor's wallet, for the test USDC its evaluation names.

This is the only path by which Tamga pays a bill; a bill paid by bank transfer is the owner's to
pay, and the executor never touches it (ADR-0027). It reads what to pay from an approved decision
and nothing else: no message, prompt or model output reaches it. The payment's idempotency key
is written before the call is sent, so a call sent again (a timeout, a crash, the executor
signed out) is the same Circle transaction, and the vault pays a bill's key once. A decision is
marked settled only with the transaction's hash. A replay company records what it would have
released and never calls Circle (ADR-0009).
"""

import asyncio
import logging
import uuid
from typing import Final

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.decisions import service as decisions
from app.modules.decisions.schemas import DecisionOut
from app.modules.policy import service as policies
from app.modules.settlement import repository
from app.modules.settlement.models import Payment, PaymentState
from app.modules.settlement.service import payee_addresses
from app.platform import agent_wallet, chain
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

_RELEASE: Final = "release(bytes32,bytes32,address,uint256)"


async def pay_approved() -> int:
    """Pays every approved decision not paid yet, the earliest approved first. Returns how many
    were paid. Where no vault or executor is configured, nothing is sent."""
    async with open_session() as db:
        company_ids = await decisions.companies_with_approved(db)
    paid = 0
    for company_id in company_ids:
        async with open_session() as db:
            company = (await companies.get_companies(db, [company_id])).get(company_id)
            approved = await decisions.approved(db, company_id)
        if company is None:
            continue
        for decision in approved:
            if not policies.tamga_pays(decision.method):
                continue
            try:
                paid += await _pay(company_id, company.mode, decision)
            except Exception as error:  # one payment that fails never stops the others
                logger.warning("paying decision %s failed: %s", decision.id, type(error).__name__)
    return paid


async def _pay(company_id: uuid.UUID, mode: CompanyMode, decision: DecisionOut) -> int:
    settings = get_settings()
    vault = settings.policy_vault_address
    if mode is not CompanyMode.REPLAY and (vault is None or settings.agent_wallet_address is None):
        return 0
    key = "0x" + decisions.payment_key(decision.invoice_id).hex()
    async with open_session() as db:
        payee = (await payee_addresses(db, company_id, [decision.party_id])).get(decision.party_id)
        if payee is None or decision.usdc_units is None:
            # The rules hold such a bill; one whose wallet went since is evaluated again.
            await decisions.record_refused(
                db,
                company_id,
                decision.id,
                decision.evaluation,
                "The subcontractor has no wallet on the vault",
                final=False,
            )
            await db.commit()
            return 0
        payment = await repository.payment_for(db, company_id, decision.id, decision.evaluation)
        if payment is None:
            payment = Payment(
                company_id=company_id,
                decision_id=decision.id,
                evaluation=decision.evaluation,
                invoice_id=decision.invoice_id,
                method=decision.method,
                pay_currency="USDC",
                pay_amount=chain.usdc(decision.usdc_units),
                payee=payee,
                usdc_units=decision.usdc_units,
                payment_key=key,
                idempotency_key=uuid.uuid4(),
                state=(
                    PaymentState.WOULD_RELEASE
                    if mode is CompanyMode.REPLAY
                    else PaymentState.SUBMITTING
                ),
            )
            await repository.add(db, payment)
            if mode is CompanyMode.REPLAY:
                await decisions.record_would_release(
                    db, company_id, decision.id, decision.evaluation, payee
                )
        elif payment.state is not PaymentState.SUBMITTING:
            # Its result and the decision's are written together: nothing is left to do.
            return 0
        sending = payment.state is PaymentState.SUBMITTING
        idempotency_key, units = payment.idempotency_key, payment.usdc_units
        await db.commit()
    if not sending or vault is None or idempotency_key is None or units is None:
        return 0

    try:
        done = await agent_wallet.execute(
            _RELEASE,
            ["0x" + chain.company_key(company_id).hex(), key, payee, units],
            contract=vault,
            idempotency_key=idempotency_key,
        )
    except agent_wallet.WalletError as error:
        if not error.ended:
            # Signed out, not final yet, or the CLI not reached: the same key goes again on the
            # next run, which Circle answers with the same transaction.
            logger.warning("paying decision %s waits: %s", decision.id, error.code)
            return 0
        # Refused by the vault. A cap, the balance or a payee not active changed on the chain
        # since the evaluation: the bill goes back to the owner, and the rules, reading the same
        # chain, see why. Anything else (a key it had paid, the executor or the company unknown
        # to it, USDC's blocklist) would pass every rule again and fail the same way: it is for
        # someone to look at.
        final = not await _changed_since(company_id, decision.invoice_id, payee, units)
        async with open_session() as db:
            payment = await repository.payment_for(db, company_id, decision.id, decision.evaluation)
            if payment is not None:
                payment.state, payment.failure = PaymentState.FAILED, str(error)[:300]
                payment.finished_at = utcnow()
            await decisions.record_refused(
                db, company_id, decision.id, decision.evaluation, str(error), final=final
            )
            await db.commit()
        return 0

    async with open_session() as db:
        payment = await repository.payment_for(db, company_id, decision.id, decision.evaluation)
        if payment is not None:
            payment.state = PaymentState.SETTLED
            payment.tx_hash, payment.circle_transaction_id = done.tx_hash, done.transaction_id
            payment.finished_at = utcnow()
            payment.paid_on = payment.finished_at.date()
        await decisions.record_paid(
            db,
            company_id,
            decision.id,
            decision.evaluation,
            tx_hash=done.tx_hash,
            transaction_id=done.transaction_id,
        )
        await db.commit()
    return 1


async def _changed_since(
    company_id: uuid.UUID, invoice_id: uuid.UUID, payee: str, units: int
) -> bool:
    """Whether what the chain says now explains a refusal: the bill's key unpaid, and a cap, the
    balance or the payee's place on the company's list no longer allowing the release."""
    if await chain.is_paid(company_id, decisions.payment_key(invoice_id)):
        return False
    state, active_from = await asyncio.gather(
        chain.company_state(company_id), chain.payee_active_from(company_id, payee)
    )
    if state is None:
        return False
    return (
        units > min(state.per_payment_cap, state.left_today, state.balance)
        or not active_from
        or active_from > int(utcnow().timestamp())
    )
