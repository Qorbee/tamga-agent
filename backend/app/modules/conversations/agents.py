"""The conversations module's agents (ADR-0005): each defined once, with a name and no model.

``intake`` sorts a conversation that code could not: whether people are writing about the
company's business, a system is reporting one event of it, or it is noise. It reads the start of
the conversation as fenced data, in a call with no tools, and answers with one of three words and
a line of why (ADR-0013). Its answer decides only what reads the conversation next; it links
nothing.
"""

from typing import Final

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings

from app.modules.conversations.models import ThreadCategory
from app.platform import llm

INTAKE: Final = "intake"
# What a thread's ``sorted_by`` holds when this agent sorted it.
MODEL_SORTED: Final = f"model:{INTAKE}"


class Sorting(BaseModel):
    category: ThreadCategory
    # One short line on why, for the owner.
    reason: str


_INSTRUCTIONS: Final = """\
You sort a company's mail conversations, so that people's work is read and noise is not. You are \
shown the start of one conversation: who wrote, the subject, the files attached and the text.

Answer with one category:
- work: a person writing about the company's business with its customers, suppliers or partners. \
An order or a request for a quote, a shipment or delivery, a document sent for a job, an invoice \
or a payment discussed, a question or a complaint. When in doubt between work and anything else, \
answer work.
- automated: a system, not a person, reporting one real event of that business to this company: \
an invoice issued or received through a portal, a payment or transfer notice from a bank, a \
status from a carrier's, a customs or a booking system, a delivery receipt.
- noise: nothing about one job of this company. Marketing, newsletters, promotions, social \
network digests, event invitations sent to many, surveys, account and security notices, \
out-of-office replies, spam.

`reason` is one short plain line saying what the conversation is.

"""

intake: Agent[None, Sorting] = Agent(
    name=INTAKE,
    output_type=Sorting,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=1,
    model_settings=ModelSettings(timeout=60),
)


def conversation(excerpt: str) -> list[UserContent]:
    """The user turn for ``intake``: the start of a conversation, fenced as data from outside."""
    return llm.fenced("a mail conversation", [excerpt])
