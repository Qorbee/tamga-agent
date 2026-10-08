"""The controller's opinion on each bill that waits for the owner (05, Decisions today;
ADR-0002): once an evaluation needs an approval, the controller weighs it and proposes pay, pay
part, hold or ask, with its reason. The opinion is kept with that evaluation and in the log. It
moves nothing: the rules decided what can be approved, and the owner approves.

What code knows (the amounts, the rules' results, the policy, the dates) is told as it is; what
came from outside (the books' words, the firms', the jobs' as read from mail) is fenced as data
(ADR-0013). An evaluation is weighed again when anything the controller was shown changed, or
once its opinion is ADVICE_FRESH old, since time alone changes what is due. Every request is
written before it is sent and counted in ADVICE_PER_DAY a company, whatever comes back, and one
is never sent twice for the same inputs; at zero no bill's details leave for the model's
provider.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from pydantic_ai.usage import UsageLimits

from app.config import get_settings
from app.modules.books import service as books
from app.modules.books.schemas import InvoiceOut
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.decisions import agents, repository, service
from app.modules.decisions.models import Decision
from app.modules.policy import service as policies
from app.modules.policy.schemas import PolicyOut
from app.modules.projects import service as projects
from app.modules.projects.schemas import ProjectOut
from app.platform import chain, clock, llm
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

# How long an opinion on unchanged inputs stands before it is weighed again.
ADVICE_FRESH: Final = timedelta(days=3)
# Bills weighed for a company in one pass: a backlog is worked through over the passes.
ADVICE_BATCH: Final = 10
# How much of a bill's description the controller is shown.
DESCRIPTION_CHARS: Final = 600
# One answer, and one more when the first could not be used.
_LIMITS: Final = UsageLimits(request_limit=2)


@dataclass(frozen=True)
class _Due:
    """An evaluation to weigh, and what the controller is shown of it but the day."""

    decision_id: uuid.UUID
    evaluation: int
    amount: Decimal
    # The SHA-256 of ``code`` and ``outside``: a request is never sent twice for the same.
    inputs: str
    code: list[str]
    outside: list[str]


async def advise_pending() -> int:
    """Has the controller weigh every company's evaluations that wait for the owner and have no
    opinion on what they are now. Returns how many opinions were kept."""
    if get_settings().advice_per_day == 0:
        return 0
    async with open_session() as db:
        company_ids = await service.companies_awaiting_owner(db)
    advised = 0
    for company_id in company_ids:
        try:
            advised += await advise_company(company_id)
        except Exception as error:  # one company that fails never stops the others
            logger.warning("advising on %s failed: %s", company_id, type(error).__name__)
    return advised


async def advise_company(company_id: uuid.UUID) -> int:
    """One company's pass, the earliest evaluated first. A failure outside the answer (the
    provider, the network) leaves the rest for the next pass; the request it was is not sent
    again."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        # A replay's decisions are made at its own clock's time, never here (ADR-0009).
        if company is None or company.mode is CompanyMode.REPLAY:
            return 0
        left = await service.advice_left(db, company_id)
        if left == 0:
            return 0
        waiting = list(await repository.awaiting_owner(db, company_id))
        bills = {
            bill.id: bill
            for bill in await books.get_invoices(db, company_id, [d.invoice_id for d in waiting])
        }
        jobs = await service.jobs_of(db, company_id, [d.invoice_id for d in waiting])
        known = await projects.describe_projects(
            db, company_id, sorted({job for held in jobs.values() for job in held})
        )
        policy = await policies.current_policy(db, company_id)
        asked = await repository.advice_asked(
            db, company_id, [d.id for d in waiting], utcnow() - ADVICE_FRESH
        )
        today = clock.now(company).date()
        due: list[_Due] = []
        for decision in waiting:
            bill = bills.get(decision.invoice_id)
            if bill is None:
                continue
            held = [known[job] for job in jobs.get(decision.invoice_id, []) if job in known]
            code, outside = _shown(decision, bill, held, policy)
            inputs = _digest(code, outside)
            each = _Due(decision.id, decision.evaluation, decision.amount, inputs, code, outside)
            if (each.decision_id, each.evaluation, each.inputs) not in asked:
                due.append(each)
    trace_content = companies.traces_content(company.kind)
    kept = 0
    for each in due[: min(ADVICE_BATCH, left)]:
        async with open_session() as db:
            await service.ask_advice(db, company_id, each.decision_id, each.evaluation, each.inputs)
        try:
            with llm.traced(agents.CONTROLLER) as trace_id:
                run = await llm.run(
                    agents.controller,
                    agents.weighed([f"Today: {today.isoformat()}", *each.code], each.outside),
                    deps=each.amount,
                    limits=_LIMITS,
                    language=company.language,
                    trace_content=trace_content,
                )
        except llm.NoAnswer as error:
            logger.warning("the controller gave no proposal for %s: %s", each.decision_id, error)
            continue
        except Exception as error:
            # The error's type only: a provider's error can quote what it was sent.
            logger.warning("advising on %s failed: %s", each.decision_id, type(error).__name__)
            break
        body = _body(each.evaluation, each.inputs, run.output, trace_id)
        async with open_session() as db:
            if await service.keep_advice(db, company_id, each.decision_id, each.evaluation, body):
                kept += 1
    return kept


def _body(
    evaluation: int, inputs: str, advice: agents.Advice, trace_id: str | None
) -> dict[str, Any]:
    return {
        "evaluation": evaluation,
        "proposal": advice.proposal,
        "amount": str(advice.amount) if advice.amount is not None else None,
        "reason": advice.reason,
        **llm.made_by(agents.controller).stored(),
        "inputs": inputs,
        "trace_id": trace_id,
    }


def _digest(code: list[str], outside: list[str]) -> str:
    return hashlib.sha256(json.dumps([code, outside], ensure_ascii=False).encode()).hexdigest()


def _shown(
    decision: Decision, bill: InvoiceOut, jobs: list[ProjectOut], policy: PolicyOut
) -> tuple[list[str], list[str]]:
    """What the controller is shown of one evaluation, but the day: what code knows, and what
    came from outside."""
    currency = decision.currency or ""
    usdc = (
        f" (paid as {chain.usdc(decision.usdc_units)} test USDC)"
        if decision.usdc_units is not None
        else ""
    )
    code = [
        f"To pay: {decision.amount} {currency}{usdc}"
        + (", an amount the owner set" if decision.amount_edited else ""),
        f"Policy: approval limit {policy.approval_limit} {policy.currency}; "
        + (
            "pay-when-paid, a job's subcontractor paid once its client paid"
            if policy.pay_when_paid
            else "no pay-when-paid"
        ),
        "Rules:",
        *(f"- {rule['rule']}: {rule['outcome']}, {rule['reason']}" for rule in decision.rules),
    ]
    outside = [
        f"Bill {bill.number or '(no number)'} from {bill.party_name or '(unknown)'}",
        f"Issued {_day(bill.issue_date)}, due {_day(bill.due_date)}",
        f"Total {_money(bill.total_amount, currency)}; "
        f"left to pay {_money(bill.remaining_amount, currency)}",
        *([f"Its description: {_cut(bill.description)}"] if bill.description else []),
        *(_job(job) for job in jobs),
    ]
    if not jobs:
        outside.append("No job holds it as evidence.")
    return code, outside


def _job(job: ProjectOut) -> str:
    read = job.job
    parts = [f"Job {job.number}: {job.name}", f"status {job.status.value}"]
    if job.starts_on or job.ends_on:
        parts.append(f"days {_day(job.starts_on)} to {_day(job.ends_on)}")
    if read is not None:
        if read.client:
            parts.append(f"client {read.client}")
        if read.origin or read.destination:
            parts.append(f"from {read.origin or '?'} to {read.destination or '?'}")
        if read.cargo:
            parts.append(f"cargo {read.cargo}")
        if read.trucks:
            parts.append(f"{read.trucks} trucks expected")
    return "; ".join(parts)


def _cut(text: str) -> str:
    """A description as the books hold it, one line, cut where a bill's purpose has long been
    said: a record from outside never makes a request as long as it likes."""
    line = " ".join(text.split())
    return line if len(line) <= DESCRIPTION_CHARS else line[:DESCRIPTION_CHARS] + " [cut]"


def _day(day: date | None) -> str:
    return day.isoformat() if day else "?"


def _money(amount: Decimal | None, currency: str) -> str:
    return f"{amount} {currency}".strip() if amount is not None else "?"
