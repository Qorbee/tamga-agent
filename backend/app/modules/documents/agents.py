"""The documents module's agents (ADR-0005): each defined once, with a name and no model.

``reader`` is the vision model's half of reading a file: a photo, or the pages of a PDF that hold
no text layer. It is shown pictures, as a person sees them, in a call with no tools, and answers
with what is written on them (ADR-0013). What it wrote is text a model read, kept apart from what
code read (``documents.vision_pages``), and the facts found in it say so (``MODEL_READ``).

``typist`` says what kind of paper a read file is, when code could not: from its name, the
reader's line on it and the start of its text, in a call with no tools. It answers one of the
types it was offered or none, so a file's text can choose nothing else.
"""

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings

from app.modules.documents import reading
from app.platform import llm

READER: Final = "reader"
TYPIST: Final = "typist"
# How much of a file's text the typist is shown: its first page, near enough.
TYPIST_TEXT: Final = 1500
# The extractor a fact carries when it was found in text the reader wrote.
MODEL_READ: Final = f"model:{READER}"
# What the reader writes in place of what it cannot read.
ILLEGIBLE: Final = "[illegible]"


class PageReading(BaseModel):
    # What is written on the picture; empty when nothing is.
    text: str


class Reading(BaseModel):
    # One line on what the file is.
    shows: str
    # One reading per picture, in the order they were sent.
    pages: list[PageReading] = Field(max_length=reading.MAX_PICTURED)
    # Some of it could not be read with certainty.
    unsure: bool = False

    def hard(self) -> bool:
        """Whether a stronger model should read the file again: its reader said so, or left a
        place unread."""
        return self.unsure or any(ILLEGIBLE in page.text for page in self.pages)


_INSTRUCTIONS: Final = """\
You read pictures for a company's records: photographed or scanned invoices, delivery and \
transport papers, receipts and forms, photos of goods and vehicles, screenshots of spreadsheets \
and chats.

For each picture, in the order given, write what a person reading it would read:
- every word and number as it is written, in its own language: never translate, correct or complete;
- from top to bottom; a table as one line per row with its cells split by tabs; a form field as \
"label: value";
- handwriting and stamps too; write [illegible] where something cannot be read, never a guess;
- what is written on things in a photo: number plates, container and trailer numbers, signs, labels;
- nothing that is not in the picture. A picture with nothing written on it has empty text.

`shows` is one plain line on what the file is, such as "A signed consignment note" or \
"A photo of a loaded truck at a dock".

`unsure` is true when you could not read some of it with certainty: handwriting, a blurred, \
dark, skewed or cut-off picture, a faint stamp, print too small to make out. A clean print or \
scan is false.

"""

reader: Agent[int, Reading] = Agent(
    name=READER,
    # How many pictures the call shows.
    deps_type=int,
    output_type=Reading,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=2,
    # A file of twenty dense pages is a long answer; past this the call is given up.
    model_settings=ModelSettings(timeout=300),
)


@reader.output_validator
def _a_reading_per_picture(context: RunContext[int], reading: Reading) -> Reading:
    if len(reading.pages) != context.deps:
        raise ModelRetry(f"Return exactly {context.deps} pages: one per picture, in order.")
    return reading


def pictures(filename: str, images: Sequence[tuple[bytes, str]]) -> list[UserContent]:
    """The user turn for ``reader``: the file's name and its pictures, each as bytes and its image
    type, fenced as data from outside."""
    count = "1 picture" if len(images) == 1 else f"{len(images)} pictures"
    return llm.fenced(
        f"file: {filename} ({count})",
        [BinaryContent(data=data, media_type=media) for data, media in images],
    )


class Typing(BaseModel):
    # The key of the one type the file is; null when none fits or it cannot be told.
    type: str | None


_TYPIST_INSTRUCTIONS: Final = """\
You say what kind of paper a file is, for a freight company's records. You are given the types \
it can be, each as `key: label — what tells it apart`, then the file: its name, a line on what it \
shows when a model has seen it, and the start of its text, in any language.

Answer with the key of the one type the file is. Answer null when none fits, when it could be \
several, or when there is too little to tell: a wrong type is worse than none. A file is the type \
of what it is, not of what it mentions: a mail about an invoice is not an invoice.

"""

typist: Agent[frozenset[str], Typing] = Agent(
    name=TYPIST,
    # The keys it was offered: its answer is one of them.
    deps_type=frozenset,
    output_type=Typing,
    instructions=_TYPIST_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=1,
    model_settings=ModelSettings(timeout=60),
)


# What a small model writes for "none" when it writes a word in place of null.
_NONE: Final = frozenset({"", "null", "none"})


@typist.output_validator
def _an_offered_type(context: RunContext[frozenset[str]], typing: Typing) -> Typing:
    if typing.type is None:
        return typing
    key = typing.type.strip().lower()
    if key in _NONE:
        return Typing(type=None)
    if key not in context.deps:
        raise ModelRetry("Answer one of the keys listed, or null.")
    return Typing(type=key)


def typed(
    types: Sequence[tuple[str, str, str]], filename: str, shows: str, text: str
) -> list[UserContent]:
    """The user turn for ``typist``: the types it may answer, as ``(key, label, hint)``, then the
    file fenced as data from outside."""
    offered = "\n".join(
        f"- {key}: {label}" + (f" — {hint}" if hint else "") for key, label, hint in types
    )
    seen = f"shows: {shows}\n" if shows else ""
    return [
        f"Types:\n{offered}\n\n",
        *llm.fenced(f"file: {filename}", [f"{seen}text:\n{text[:TYPIST_TEXT]}"]),
    ]
