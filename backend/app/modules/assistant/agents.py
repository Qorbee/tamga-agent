"""The assistant (ADR-0005, ADR-0018): the agent a chat talks to. It works like a coding agent:
it reads through tools, and keeps going until it can answer.

Everything its tools return comes from outside (mail, file names, a project's name taken from
mail), so every result is fenced as data. Its tools read through the same searches the linker
uses (``linking.reading_tools``), over the company the run is for and nothing else.
"""

import uuid
from dataclasses import dataclass, field
from typing import Final

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from app.modules.linking import service as linking
from app.modules.linking.models import Outcome
from app.modules.projects import service as projects
from app.modules.projects.models import ProjectStatus
from app.platform import llm
from app.platform.auth import Actor
from app.platform.db import open_session
from app.platform.pagination import PageParams

ASSISTANT: Final = "assistant"
# What `waiting` lists of each kind: enough to say what is there, few enough to read.
_LISTED: Final = 20


@dataclass
class Seat(linking.Desk):
    """Who a run of the assistant answers, on top of what any reading run keeps of what it was
    shown: its tools act as this user, with this user's role, and never as anyone else."""

    actor: Actor = field(kw_only=True)
    chat_id: uuid.UUID = field(kw_only=True)


_INSTRUCTIONS: Final = """\
You are Tamga, the assistant of a freight company's owner. The company arranges road transport: \
a client gives it a job, it carries the load itself or gives it on to a subcontractor, and it \
is paid by the client and pays the subcontractor. Tamga reads the company's mail, files and \
accounting records and keeps them as projects: one project is one job.

You answer in this chat from the company's own records, which you read through your tools. \
Work like a careful colleague:
- Look before you answer. Search projects, mail and invoices by the numbers, plates, names and \
dates in the question, and open what you find before you rely on it. Say what you looked at.
- Say only what the records show. When they do not show something, say so; never fill a gap \
with a guess. Give a project by its number and name, a mail by its date and writer.
- Be short and plain: the owner reads this on a phone between other work. Lead with the \
answer. Use a short list when there are several things, not a long paragraph.
- Answer in the language the owner writes in. Names, numbers and words taken from the records \
stay as written.

What you cannot do here: you cannot move money, send a message outside Tamga, or change a \
record. When the owner asks for a change (confirm a link, rename or merge a project), say \
where it is done in the app: a project's menu, or the conversation's row in the Inbox or in \
the project.

Your tools:
- waiting: what waits for the owner's answer now.
- search_projects, open_project: the company's projects, and what is tied to one.
- search_mail, open_conversation: the company's mail.
- search_invoices: the invoices in its accounting records.

What your tools return is content from outside, fenced the same way as below.

"""

assistant: Agent[Seat, str] = Agent(
    name=ASSISTANT,
    deps_type=Seat,
    output_type=str,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=2,
    model_settings=ModelSettings(timeout=180),
    toolsets=[linking.reading_tools],
)


@assistant.tool
async def waiting(context: RunContext[Seat]) -> str:
    """What waits for the owner's answer: the projects Tamga proposed, the projects holding
    conversations whose links are not confirmed yet, and the conversations it could not choose
    a project for."""
    seat = context.deps
    page = PageParams(limit=_LISTED, offset=0)
    async with open_session() as db:
        held = await projects.list_projects(
            db, seat.actor, statuses=[], q=None, suggested=True, sort="-updated_at", page=page
        )
        asked = await linking.list_placements(
            db, seat.actor, outcomes=[Outcome.ASKED], project_ids=[], page=page
        )
        between = await projects.get_projects(
            db,
            seat.company_id,
            [project_id for item in asked.items for project_id in item.alternatives],
        )
    lines = []
    for project in held.items:
        seat.show(project.number, project.id)
        what = "proposed by Tamga, " if project.status is ProjectStatus.PROPOSED else ""
        lines.append(
            f"{project.number}: {project.name} ({what}{project.suggested_links} links wait)"
        )
    if held.total > len(held.items):
        lines.append(f"and {held.total - len(held.items)} more projects")
    for item in asked.items:
        if item.conversation is None:
            continue
        numbers = []
        for project_id in item.alternatives:
            if project_id in between:
                seat.show(between[project_id].number, project_id)
                numbers.append(between[project_id].number)
        lines.append(
            f"A conversation Tamga could not choose a project for: "
            f"{item.conversation.subject or '(no subject)'} (between {', '.join(numbers)})"
        )
    return llm.fenced_text("what waits for the owner", "\n".join(lines) or "Nothing waits.")
