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
from datetime import date, datetime, timedelta
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Final

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.conversations import service as conversations
from app.modules.decisions import service as decisions
from app.modules.playground import repository
from app.modules.playground.models import World
from app.modules.playground.schemas import (
    Choices,
    Medium,
    PersonaOut,
    Relation,
    Said,
    ScenarioOut,
    ScenarioPolicy,
    WorldOut,
)
from app.modules.policy import service as policies
from app.modules.policy.schemas import PolicyIn
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
    relation: Relation = "carrier"

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
class Earlier:
    """A message from before the sandbox opened, so it opens on work already under way: the
    persona's, or the company's own to them (``ours``)."""

    persona: str
    days_before: int
    text: str
    ours: bool = False


@dataclass(frozen=True, slots=True)
class Scenario:
    key: str
    title: str
    summary: str
    seed: Seed
    personas: tuple[Persona, ...] = ()
    steps: tuple[Step, ...] = field(default=())
    # The company the judge runs and who they are in it, unless they name others.
    company: str = "Sandbox"
    owner: str = "Guest"
    # The scenario's policy, written before its own seed runs, with what the judge changed.
    policy: PolicyIn | None = None
    history: tuple[Earlier, ...] = ()


_scenarios: dict[str, Scenario] = {}


def register_scenario(scenario: Scenario) -> None:
    """Adds a scenario a judge can open a sandbox with; the first registered is the default."""
    if scenario.key in _scenarios:
        raise ValueError(f"scenario {scenario.key} registered twice")
    if any(a.day > b.day for a, b in zip(scenario.steps, scenario.steps[1:], strict=False)):
        raise ValueError(f"scenario {scenario.key}: steps out of order")
    keys = {persona.key for persona in scenario.personas}
    if any(earlier.persona not in keys for earlier in scenario.history):
        raise ValueError(f"scenario {scenario.key}: its history names someone not in it")
    if any(earlier.days_before < 1 for earlier in scenario.history):
        raise ValueError(f"scenario {scenario.key}: its history is dated from a day before")
    _scenarios[scenario.key] = scenario


def list_scenarios() -> list[ScenarioOut]:
    return [_out(scenario) for scenario in _scenarios.values()]


def _out(scenario: Scenario) -> ScenarioOut:
    policy = scenario.policy
    return ScenarioOut(
        key=scenario.key,
        title=scenario.title,
        summary=scenario.summary,
        company=scenario.company,
        owner=scenario.owner,
        policy=ScenarioPolicy(
            currency=policy.currency,
            approval_limit=policy.approval_limit,
            reserve_rule=policy.reserve_rule,
            payment_weekday=policy.payment_weekday,
        )
        if policy
        else None,
    )


def names(key: str) -> tuple[str, str]:
    """The company and the owner a scenario opens with when the judge names neither."""
    scenario = _scenarios[key]
    return scenario.company, scenario.owner


def _policy(scenario: Scenario, choices: Choices | None) -> PolicyIn | None:
    """The scenario's policy with the judge's choices in it, checked as any policy is."""
    if scenario.policy is None:
        return None
    if choices is None:
        return scenario.policy
    try:
        return PolicyIn.model_validate(
            {**scenario.policy.model_dump(), **choices.model_dump(exclude_none=True)}
        )
    except ValidationError as error:
        raise BadRequest(f"Those payment rules do not hold: {error.errors()[0]['msg']}") from None


def chosen(key: str | None) -> str:
    """The scenario a new sandbox opens with: the one asked for, else the default."""
    if key is None:
        if not _scenarios:
            raise BadRequest("No scenario is registered")
        return next(iter(_scenarios))
    if key not in _scenarios:
        raise BadRequest(f"No scenario {key}")
    return key


async def seed_later(
    db: AsyncSession, company_id: uuid.UUID, scenario: str, choices: Choices | None = None
) -> None:
    """Records a new sandbox's scenario and queues its seeding, in the caller's transaction, so
    it runs once the sandbox exists and never for one whose creation rolled back. The judge's
    choices are checked here, so a policy that cannot hold is refused before anything opens."""
    from app.modules.playground import tasks

    _policy(_scenarios[scenario], choices)
    await repository.add(db, World(company_id=company_id, scenario=scenario))
    args: dict[str, object] = {"company_id": str(company_id)}
    if choices is not None:
        args["choices"] = choices.model_dump(mode="json", exclude_none=True)
    await jobs.defer(db, tasks.seed, args=args)


async def seed(company_id: uuid.UUID, choices: Choices | None = None) -> None:
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
    policy = _policy(scenario, choices)
    if policy is not None:
        # Before the scenario's own seed, which then finds a version and keeps it.
        async with open_session() as db:
            await policies.seed_policy(db, company_id, policy)
    await scenario.seed(company_id)
    await _remember(company_id, scenario)
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


# Minutes between two earlier messages: a day's few fit in its morning.
_APART: Final = 20


async def _remember(company_id: uuid.UUID, scenario: Scenario) -> None:
    """The messages from before the sandbox opened, each dated that many days back, in office
    hours and in the order written, so a reply never shows above what it answers. Each has a
    Message-ID of its own, so seeding again stores none twice."""
    personas = {persona.key: persona for persona in scenario.personas}
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id]))[company_id]
        now = clock.now(company)
        for index, earlier in enumerate(scenario.history):
            await _say(
                db,
                company_id,
                personas[earlier.persona],
                earlier.text,
                message_id=f"<earlier-{index}.{company_id}@world.sandbox.example>",
                at=(now - timedelta(days=earlier.days_before)).replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
                + timedelta(minutes=_APART * index),
                ours=earlier.ours,
            )
        await db.commit()


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
                relation=p.relation,
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
    at: datetime | None = None,
    ours: bool = False,
) -> None:
    """A message in a persona's thread: theirs, or with ``ours`` the company's own to them, as
    its mailbox would have kept what it sent. ``{company}`` in the text is the company's name."""
    company = (await companies.get_companies(db, [company_id]))[company_id]
    mail = EmailMessage()
    # As addresses, not text: a judge's company named "Acme, Inc." would read as two senders.
    own = Address(display_name=company.name, addr_spec=OWNER_ADDRESS)
    theirs = Address(display_name=persona.name, addr_spec=persona.address)
    mail["From"] = own if ours else theirs
    mail["To"] = theirs if ours else own
    mail["Subject"] = (
        f"WhatsApp · {persona.name}" if persona.medium == "whatsapp" else persona.subject
    )
    mail["Date"] = format_datetime(at or clock.now(company))
    mail["Message-ID"] = message_id or make_msgid(domain="world.sandbox.example")
    mail["In-Reply-To"] = persona.thread_key
    mail["References"] = persona.thread_key
    mail.set_content(text.replace("{company}", company.name))
    for name, data, content_type in files:
        main, _, sub = content_type.partition("/")
        mail.add_attachment(data, maintype=main, subtype=sub, filename=name)
    await conversations.ingest_simulated(
        db, company_id, mail.as_bytes(), sent_from=OWNER_ADDRESS if ours else None
    )
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
