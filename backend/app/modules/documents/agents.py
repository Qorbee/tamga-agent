"""The documents module's agents (ADR-0005): each defined once, with a name and no model.

``reader`` is the vision model's half of reading a file: a photo, or the pages of a PDF that hold
no text layer. It is shown pictures, as a person sees them, in a call with no tools, and answers
with what is written on them (ADR-0013). What it wrote is text a model read, kept apart from what
code read (``documents.vision_pages``), and the facts found in it say so (``MODEL_READ``).
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
# The extractor a fact carries when it was found in text the reader wrote.
MODEL_READ: Final = f"model:{READER}"


class PageReading(BaseModel):
    # What is written on the picture; empty when nothing is.
    text: str


class Reading(BaseModel):
    # One line on what the file is.
    shows: str
    # One reading per picture, in the order they were sent.
    pages: list[PageReading] = Field(max_length=reading.MAX_PICTURED)


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
