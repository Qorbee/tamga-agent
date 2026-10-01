"""The languages Tamga writes in, and the web app is translated into."""

from enum import StrEnum
from typing import Final


class Language(StrEnum):
    EN = "en"
    TR = "tr"


# As an agent's instructions name each: never shown to a user.
ENGLISH_NAMES: Final = {Language.EN: "English", Language.TR: "Turkish"}
