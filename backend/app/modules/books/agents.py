"""The books' agent: ``bill_reader`` reads what a bill charges for from the text of its file,
line by line, each line quoted as the file has it. Code checks every quote and plate against
the file (books/reading.py) before anything is kept (ADR-0023)."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings

from app.modules.books.charges import OTHER, Charge
from app.platform import llm

BILL_READER: Final = "bill_reader"
# Run on the typist's model and effort: the small model that already reads files for their type.
READER_SETTINGS: Final = "typist"
# How much of a bill's text the reader is shown: a bill of many trucks runs to a few pages.
READER_TEXT: Final = 12_000


class ReadLine(BaseModel):
    # The line's words exactly as the file has them: what code looks for in the file.
    quote: str = Field(max_length=300)
    # One of the kinds offered, or "other".
    charge: str
    # The line's amount before tax, as the file gives it.
    amount: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    # The plates the line names, as written.
    plates: list[str] = []
    # Days of waiting the line charges for.
    days: int | None = Field(default=None, ge=0, le=365)
    origin: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=200)


class BillRead(BaseModel):
    # What the bill is for, in a line.
    about: str = Field(max_length=300)
    kind: Literal["invoice", "return", "credit"]
    lines: list[ReadLine] = Field(default=[], max_length=60)


_INSTRUCTIONS: Final = """\
You read one bill of a freight company: what it charges for, line by line. You are given the \
kinds of charge a line can be, each as `key: what tells it apart`, the bill as the company's \
books hold it, and the text of the bill's file, in any language.

For each line that charges for something, give its words exactly as the file has them (a copy, \
never a summary, at most 300 characters), the kind of charge (one key from the list, or \
"other"), its amount before tax and its currency as the file gives them, the plates it names \
as written, the days of waiting it charges for, and where it goes from and to when it says. \
Leave out what the line does not say. Then say in one line what the bill is for, in the \
company's language ("Freight Gebze → Zakho for three trucks, with two days' waiting"), and \
whether it is an invoice, a return (it gives back goods or reverses an earlier bill) or a \
credit note. Use only what the file says.

"""

bill_reader: Agent[frozenset[str], BillRead] = Agent(
    name=BILL_READER,
    # The keys of the charges offered: a line's charge is one of them.
    deps_type=frozenset,
    output_type=BillRead,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=1,
    model_settings=ModelSettings(timeout=90),
)


@bill_reader.output_validator
def _offered_charges(context: RunContext[frozenset[str]], read: BillRead) -> BillRead:
    """A charge not offered is "other": the line still counts toward the bill's sum."""
    for line in read.lines:
        key = line.charge.strip().lower()
        line.charge = key if key in context.deps else OTHER
    return read


def bill(charges: Sequence[Charge], header: Sequence[str], text: str) -> list[UserContent]:
    """The user turn: the kinds of charge, then the bill's header and its file's text, both
    fenced as data from outside."""
    offered = "\n".join(f"- {charge.key}: {charge.hint}" for charge in charges)
    return [
        f"Kinds of charge:\n{offered}\n\n",
        *llm.fenced("the bill in the books", ["\n".join(header)]),
        *llm.fenced("the text of the bill's file", [text[:READER_TEXT]]),
    ]
