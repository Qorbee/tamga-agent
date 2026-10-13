from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# How a persona writes: a chat on WhatsApp, or mail.
Medium = Literal["whatsapp", "mail"]


class ScenarioOut(BaseModel):
    key: str
    title: str
    summary: str


class PersonaOut(BaseModel):
    key: str
    name: str
    # Who they are to the company: "Dispatcher, Kestrel Freight".
    role: str
    # The address their messages come from; the World panel's thread is theirs.
    address: str
    thread_key: str
    medium: Medium
    # A mail persona's subject; empty on WhatsApp.
    subject: str


class WorldOut(BaseModel):
    """A sandbox's world: its scenario, its clock, who plays in it, and what the script holds."""

    scenario: ScenarioOut | None
    # The sandbox's business time: the wall clock plus every skip.
    now: datetime
    days_skipped: int
    personas: list[PersonaOut]
    # Seeded: until then its clock does not move.
    ready: bool
    # The script's steps played so far, and how many it has.
    played: int
    steps: int


class Skip(BaseModel):
    # A day at a time up to a week: "skip 24 hours", or to the next pay day.
    days: int = Field(default=1, ge=1, le=7)


class Said(BaseModel):
    """What the judge types as a persona: it arrives as that persona's message, through the
    same intake as theirs."""

    persona: str = Field(max_length=40)
    # A WhatsApp persona's is at most 200 characters, a mail persona's 1,000.
    text: str = Field(min_length=1, max_length=1000)
