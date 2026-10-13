"""The playground (ADR-0004, ADR-0003): a judge's sandbox, the scenario it opens with, and the
world that plays around it on the sandbox's own clock.

Identity creates a sandbox empty, with its guest and session, and asks for it to be seeded with
the scenario the judge picked. The scenarios packs register here fill it in a job of their own,
so the guest's click does not wait on them, and its account on the vault is opened after them.
A scenario writes only into the sandbox it is given, through the services that own each record,
as any other source of that record would.

A scenario's script is what happens later in its world: a persona's message, a client's payment
reaching the bank. Each step is due a number of the sandbox's days after it opened, and plays
when the clock passes it, by a skip or by time. A persona's message is a mail on the simulated
channel, read by the same intake as a real mailbox's, so no step can reach a real person.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.conversations import service as conversations
from app.modules.decisions import service as decisions
from app.modules.playground import repository
from app.modules.playground.models import World
from app.modules.playground.schemas import Medium, PersonaOut, Said, ScenarioOut, WorldOut
from app.modules.settlement import service as settlement
from app.platform import clock, events, jobs
from app.platform.auth import Actor, Role
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound

logger = logging.getLogger(__name__)

# Where the world's mail is addressed: nobody's mailbox, on a domain reserved for examples.
OWNER_ADDRESS: Final = "owner@sandbox.example"
# What the judge may type as a persona: a chat line, or a mail.
SAID_LENGTH: Final[dict[str, int]] = {"whatsapp": 200, "mail": 1000}

# Fills one sandbox, in sessions of its own, and commits what it writes.
type Seed = Callable[[uuid.UUID], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Persona:
    """Someone in the world who writes to the company, on WhatsApp or by mail: one thread each."""

    key: str
    name: str
    role: str
    address: str
    medium: Medium = "whatsapp"
    # A mail persona's thread: the subject every message of theirs carries.
    subject: str = ""

    @property
    def thread_key(self) -> str:
        # Every message names this as the one it answers, so the intake keeps them one thread.
        return f"<{self.key}.chat@world.sandbox.example>"


@dataclass(frozen=True, slots=True)
class Step:
    # Due this many of the sandbox's days after it opened; 0 plays right after the seed.
    day: int
    play: Seed


@dataclass(frozen=True, slots=True)
class Scenario:
    key: str
    title: str
    summary: str
    seed: Seed
    personas: tuple[Persona, ...] = ()
    steps: tuple[Step, ...] = field(default=())


_scenarios: dict[str, Scenario] = {}


def register_scenario(scenario: Scenario) -> None:
    """Adds a scenario a judge can open a sandbox with; the first registered is the default."""
    if scenario.key in _scenarios:
        raise ValueError(f"scenario {scenario.key} registered twice")
    if any(a.day > b.day for a, b in zip(scenario.steps, scenario.steps[1:], strict=False)):
        raise ValueError(f"scenario {scenario.key}: steps out of order")
    _scenarios[scenario.key] = scenario


def list_scenarios() -> list[ScenarioOut]:
    return [_out(scenario) for scenario in _scenarios.values()]


def _out(scenario: Scenario) -> ScenarioOut:
    return ScenarioOut(key=scenario.key, title=scenario.title, summary=scenario.summary)


def chosen(key: str | None) -> str:
    """The scenario a new sandbox opens with: the one asked for, else the default."""
    if key is None:
        if not _scenarios:
            raise BadRequest("No scenario is registered")
        return next(iter(_scenarios))
    if key not in _scenarios:
        raise BadRequest(f"No scenario {key}")
    return key


async def seed_later(db: AsyncSession, company_id: uuid.UUID, scenario: str) -> None:
    """Records a new sandbox's scenario and queues its seeding, in the caller's transaction, so
    it runs once the sandbox exists and never for one whose creation rolled back."""
    from app.modules.playground import tasks

    await repository.add(db, World(company_id=company_id, scenario=scenario))
    await jobs.defer(db, tasks.seed, args={"company_id": str(company_id)})


async def seed(company_id: uuid.UUID) -> None:
    """Runs a sandbox's scenario. Anything but a live sandbox is left alone: the job can outlive
    its sandbox's expiry, and it must never write into a pilot's records."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        world = await repository.get(db, company_id)
    if company is None or company.kind is not CompanyKind.SANDBOX or world is None:
        logger.warning("not seeding %s: it is no sandbox", company_id)
        return
    scenario = _scenarios.get(world.scenario)
    if scenario is None:
        logger.warning("not seeding %s: no scenario %s", company_id, world.scenario)
        return
    await scenario.seed(company_id)
    async with open_session() as db:
        world = await repository.get(db, company_id, lock=True)
        if world is not None and world.opened_on is None:
            world.opened_on = clock.now(company).date()
        # Its account on the vault is opened once its books say whom it owes, its payees.
        await settlement.open_later(db, company_id, settlement.sandbox_terms())
        await decisions.evaluate_later(db, company_id)
        # The judge's page was open while it was seeded: everything it shows is new.
        for kind in events.EventKind:
            await events.publish(db, company_id, kind)
        await db.commit()
    await play_due(company_id)


async def opened_on(company_id: uuid.UUID) -> date | None:
    """The sandbox's day when its scenario was seeded: what a script's dates count from."""
    async with open_session() as db:
        world = await repository.get(db, company_id)
    return world.opened_on if world else None


async def scenario_of(db: AsyncSession, company_id: uuid.UUID) -> ScenarioOut | None:
    world = await repository.get(db, company_id)
    scenario = _scenarios.get(world.scenario) if world else None
    return _out(scenario) if scenario else None


async def just_opened(db: AsyncSession) -> list[uuid.UUID]:
    """The sandboxes seeded within the last day: Tamga writes first in each, once."""
    return await repository.opened_since(db, (clock.utcnow() - timedelta(days=1)).date())


async def get_world(db: AsyncSession, actor: Actor) -> WorldOut:
    company = (await companies.get_companies(db, [actor.company_id]))[actor.company_id]
    world = await repository.get(db, actor.company_id)
    if company.kind is not CompanyKind.SANDBOX or world is None:
        raise NotFound("Only a sandbox has a world")
    scenario = _scenarios.get(world.scenario)
    return WorldOut(
        scenario=_out(scenario) if scenario else None,
        now=clock.now(company),
        days_skipped=company.clock_offset.days,
        personas=[
            PersonaOut(
                key=p.key,
                name=p.name,
                role=p.role,
                address=p.address,
                thread_key=p.thread_key,
                medium=p.medium,
                subject=p.subject,
            )
            for p in (scenario.personas if scenario else ())
        ],
        ready=world.opened_on is not None,
        played=world.played,
        steps=len(scenario.steps) if scenario else 0,
    )


async def skip(db: AsyncSession, actor: Actor, days: int) -> WorldOut:
    """Moves the sandbox's clock forward, then plays what its script holds for the days passed
    and has its bills weighed again on the new day. Commits."""
    actor.require(Role.OWNER)
    world = await repository.get(db, actor.company_id, lock=True)
    company = (await companies.get_companies(db, [actor.company_id]))[actor.company_id]
    if company.kind is not CompanyKind.SANDBOX or world is None:
        raise NotFound("Only a sandbox's clock moves")
    # Its script counts from the day it opened: a skip before that would shift every step.
    if world.opened_on is None:
        raise Conflict("The sandbox is still being set up: skip once it has opened")
    await companies.advance_clock(db, actor.company_id, timedelta(days=days))
    await _play_later(db, actor.company_id)
    await decisions.evaluate_later(db, actor.company_id)
    # Every page counts from the company's day: all of it may read differently now.
    for kind in events.EventKind:
        await events.publish(db, actor.company_id, kind)
    await db.commit()
    await db.refresh(company)
    return await get_world(db, actor)


async def say_as(db: AsyncSession, actor: Actor, body: Said) -> None:
    """The judge types as a persona: the message arrives as theirs. Its text is data like any
    mail's (root AGENTS.md, invariant 3). Commits."""
    actor.require(Role.OWNER)
    world = await repository.get(db, actor.company_id)
    scenario = _scenarios.get(world.scenario) if world else None
    personas = scenario.personas if scenario else ()
    persona = next((p for p in personas if p.key == body.persona), None)
    if persona is None:
        raise NotFound("No such persona in this sandbox")
    if len(body.text) > SAID_LENGTH[persona.medium]:
        raise BadRequest(
            f"A {persona.medium} message is at most {SAID_LENGTH[persona.medium]} characters"
        )
    await _say(db, actor.company_id, persona, body.text)
    await db.commit()


async def say(
    company_id: uuid.UUID,
    persona: Persona,
    key: str,
    text: str,
    files: Sequence[tuple[str, bytes, str]] = (),
) -> None:
    """A script's step: a persona writes, with files if any (name, bytes, content type). ``key``
    names the step, so a step played again after a crash stores nothing twice."""
    async with open_session() as db:
        await _say(
            db,
            company_id,
            persona,
            text,
            files,
            message_id=f"<{key}.{company_id}@world.sandbox.example>",
        )
        await db.commit()


async def _say(
    db: AsyncSession,
    company_id: uuid.UUID,
    persona: Persona,
    text: str,
    files: Sequence[tuple[str, bytes, str]] = (),
    *,
    message_id: str | None = None,
) -> None:
    company = (await companies.get_companies(db, [company_id]))[company_id]
    mail = EmailMessage()
    mail["From"] = f"{persona.name} <{persona.address}>"
    mail["To"] = OWNER_ADDRESS
    mail["Subject"] = (
        f"WhatsApp · {persona.name}" if persona.medium == "whatsapp" else persona.subject
    )
    mail["Date"] = format_datetime(clock.now(company))
    mail["Message-ID"] = message_id or make_msgid(domain="world.sandbox.example")
    mail["In-Reply-To"] = persona.thread_key
    mail["References"] = persona.thread_key
    mail.set_content(text)
    for name, data, content_type in files:
        main, _, sub = content_type.partition("/")
        mail.add_attachment(data, maintype=main, subtype=sub, filename=name)
    await conversations.ingest_simulated(db, company_id, mail.as_bytes())
    await events.publish(db, company_id, events.EventKind.THREADS)


async def _play_later(db: AsyncSession, company_id: uuid.UUID) -> None:
    from app.modules.playground import tasks

    await jobs.defer(
        db, tasks.play, args={"company_id": str(company_id)}, lock=f"world:{company_id}"
    )


async def play_due(company_id: uuid.UUID) -> int:
    """Plays the script's steps the sandbox's clock has passed, in order, each marked played
    once it ran. Returns how many played. Runs under the world's job lock, or in the seed job
    before any other can be queued."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        world = await repository.get(db, company_id)
    if company is None or world is None or world.opened_on is None:
        return 0
    scenario = _scenarios.get(world.scenario)
    if scenario is None:
        return 0
    day = (clock.now(company).date() - world.opened_on).days
    played = 0
    for index in range(world.played, len(scenario.steps)):
        step = scenario.steps[index]
        if step.day > day:
            break
        await step.play(company_id)
        async with open_session() as db:
            await repository.set_played(db, company_id, index + 1)
            await decisions.evaluate_later(db, company_id)
            await db.commit()
        played += 1
    return played


async def play_due_everywhere() -> int:
    """Queues the play of each sandbox whose next step the wall clock has reached without a
    skip. Returns how many were queued."""
    lengths = {key: len(scenario.steps) for key, scenario in _scenarios.items()}
    async with open_session() as db:
        worlds = await repository.unfinished(db, lengths)
        by_id = await companies.get_companies(db, [world.company_id for world in worlds])
        queued = 0
        for world in worlds:
            company = by_id.get(world.company_id)
            if company is None or world.opened_on is None:
                continue
            step = _scenarios[world.scenario].steps[world.played]
            if (clock.now(company).date() - world.opened_on).days >= step.day:
                await _play_later(db, world.company_id)
                queued += 1
        await db.commit()
    return queued
