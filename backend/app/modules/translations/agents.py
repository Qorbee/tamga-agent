"""The translations module's agent (ADR-0005): defined once, with a name and no model.

``translator`` rewrites short texts Tamga wrote (a project's name, the reason a conversation was
placed, a route, a cargo) in another language, in a call with no tools. What it is given came from
mail in the end, so it is fenced as data from outside (ADR-0013).
"""

import re
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings

from app.platform import llm
from app.platform.language import ENGLISH_NAMES, Language

TRANSLATOR: Final = "translator"


class Translated(BaseModel):
    # One text per item given, in the order given.
    texts: list[str]


_INSTRUCTIONS: Final = """\
You translate short texts from a freight company's records: a project's name, the reason a mail \
was filed under a project, a route, a cargo. You are told the language to write in and given a \
numbered list. Return one text per item, in the order given, without its number.

- Say what the text says: add nothing, leave nothing out, keep it as short.
- A firm's or a person's name, a number, a plate, a street address, a code and a file's name stay \
exactly as written. A country or a city is written as that language writes it.
- A text already in that language is returned unchanged.

"""

translator: Agent[Sequence[str], Translated] = Agent(
    name=TRANSLATOR,
    # The texts the call holds, as they were given.
    deps_type=Sequence[str],  # type: ignore[type-abstract]  # read only, never built
    output_type=Translated,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=2,
    model_settings=ModelSettings(timeout=60),
)


# The list's own numbering, which a model sometimes copies in front of its answer.
_NUMBER: Final = re.compile(r"^\[(\d+)\]\s*")


@translator.output_validator
def _a_text_per_item(context: RunContext[Sequence[str]], translated: Translated) -> Translated:
    given = context.deps
    if len(translated.texts) != len(given):
        raise ModelRetry(f"Return exactly {len(given)} texts: one per item, in order.")
    texts = []
    for position, (source, text) in enumerate(zip(given, translated.texts, strict=True), 1):
        copied = _NUMBER.match(text)
        # The list's number is its position, and only where the text given did not itself begin
        # with one: a number that belongs to the text stays.
        own = copied is not None and int(copied[1]) == position and not _NUMBER.match(source)
        texts.append(text[copied.end() :] if copied and own else text)
    return Translated(texts=texts)


def case(language: Language, texts: Sequence[str]) -> list[UserContent]:
    """The user turn for ``translator``: the language to write in, chosen by code, and the texts
    as a numbered list, a line each, fenced as data from outside."""
    numbered = "\n".join(f"[{n}] {' '.join(text.split())}" for n, text in enumerate(texts, 1))
    return [
        f"Write in {ENGLISH_NAMES[language]}.\n",
        *llm.fenced(f"{len(texts)} texts to translate", [numbered]),
    ]
