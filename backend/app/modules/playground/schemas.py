from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# How a persona writes: a chat on WhatsApp, or mail.
Medium = Literal["whatsapp", "mail"]
# What a persona's firm is to the company: one it carries for, or one that carries for it.
Relation = Literal["client", "carrier"]


class ScenarioPolicy(BaseModel):
    """How the scenario's company pays, as the judge may change it before it opens."""

    currency: str
    approval_limit: Decimal
    reserve_rule: str
    payment_weekday: int | None


class ScenarioOut(BaseModel):
    key: str
    title: str
    summary: str
    # The company the judge runs, and who they are in it, unless they name others.
    company: str
    owner: str
    # None for a scenario whose company has no policy yet.
    policy: ScenarioPolicy | None


class Choices(BaseModel):
    """What the judge chose of the scenario's policy before it opened; the rest is the
    scenario's own."""

    approval_limit: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    reserve_rule: str | None = Field(default=None, max_length=40)
    payment_weekday: int | None = Field(default=None, ge=0, le=6)


class PersonaOut(BaseModel):
    key: str
    name: str
    # Who they are to the company: "Dispatcher, Kestrel Freight".
    role: str
    relation: Relation
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
