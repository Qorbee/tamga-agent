"""The decisions module's agent (ADR-0005, ADR-0002): defined once, with a name and no model.

``controller`` weighs a bill that waits for the owner's approval, in a call with no tools: its
picture, the rules' results and the jobs it belongs to. It proposes pay, pay part, hold or ask,
with its reason. The proposal is an opinion: it moves no state and no money. The rules have
already decided what can be approved, and the owner approves.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Final

from pydantic import BaseModel, Field, StringConstraints
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings

from app.modules.decisions.schemas import Proposal
from app.platform import llm

CONTROLLER: Final = "controller"


class Advice(BaseModel):
    # pay: the whole amount now. pay_part: some of it now, the amount below. hold: not now,
    # the reason saying until when. ask: a question for the owner, the reason asking it.
    proposal: Proposal
    # With pay_part only: what to pay now, in the bill's currency, less than the amount.
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    # One or two plain sentences, for the owner: the opinion is its reason.
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


_INSTRUCTIONS: Final = """\
You are the controller of a freight broker's payments. A subcontractor's bill waits for the \
owner's approval: every hard rule passed or left it to the owner. You are given the bill, the \
rules' results, the jobs it belongs to and what is known of them, and today's date.

Propose one of:
- pay: pay the whole amount now;
- pay_part: pay part of it now, with the amount (less than the bill's amount), when only part \
of the work is proven or the client paid only part;
- hold: not now, saying what it waits for (a due date far off, the client not paid yet, a \
paper missing that the rules do not check);
- ask: something does not add up, and the owner should say; your reason is the question.

The reason is one or two plain sentences for the owner: what you weighed, never how to approve. \
Name what you saw, such as a due date, a job or a truck; never invent what you were not given. \
Nothing is paid on your proposal: the owner approves, and code checks it again.

"""

controller: Agent[Decimal, Advice] = Agent(
    name=CONTROLLER,
    # The amount the bill would be paid: a part is less than it.
    deps_type=Decimal,
    output_type=Advice,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=1,
    model_settings=ModelSettings(timeout=120),
)


@controller.output_validator
def _a_part_is_less(context: RunContext[Decimal], advice: Advice) -> Advice:
    if advice.proposal != "pay_part":
        return advice.model_copy(update={"amount": None})
    if advice.amount is None or advice.amount >= context.deps:
        raise ModelRetry(f"With pay_part, give an amount less than {context.deps}.")
    return advice


def weighed(known: Sequence[str], outside: Sequence[str]) -> list[UserContent]:
    """The user turn for ``controller``: what code knows of the bill, then what came from outside
    (the books' and the papers' words), fenced as data."""
    return ["\n".join(known) + "\n\n", *llm.fenced("the bill and its jobs", ["\n".join(outside)])]
