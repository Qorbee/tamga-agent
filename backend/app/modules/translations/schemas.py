from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.platform.language import Language

# One screen's worth: names, reasons and job facts, each a line or two.
MAX_TEXTS = 100
MAX_CHARS = 600


class TranslationRequest(BaseModel):
    language: Language
    texts: list[Annotated[str, StringConstraints(max_length=MAX_CHARS)]] = Field(
        max_length=MAX_TEXTS
    )


class TranslationOut(BaseModel):
    # In the order asked. Null where there is no translation to give: the day's budget is spent,
    # the model's answer could not be used, or another request is having it written right now.
    # A reader shows the text as written, and may ask again later.
    texts: list[str | None]
