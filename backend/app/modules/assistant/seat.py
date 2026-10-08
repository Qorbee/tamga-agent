"""Who a run of the assistant answers, and the handles it gave what it showed."""

import uuid
from dataclasses import dataclass, field

from app.modules.linking import service as linking
from app.platform.auth import Actor


@dataclass
class Seat(linking.Desk):
    """Who a run of the assistant answers, on top of what any reading run keeps of what it was
    shown: its tools act as this user, with this user's role, and never as anyone else. A file,
    a link, a truck, a firm, an invoice or a past chat is named to the model by a handle the run
    gave it (f2, l3, t1, p4, i5, s6), so a tool can only reach what the run was shown."""

    actor: Actor = field(kw_only=True)
    chat_id: uuid.UUID = field(kw_only=True)
    # Kind ("f", "l", "t", "p", "i", "s") → handle → id, in the order they were shown.
    handles: dict[str, dict[int, uuid.UUID]] = field(default_factory=dict)
    # The company's memory as the run's instructions carry it, read at its first request and the
    # same for every other: a prompt that opens alike stays cached.
    taught: str | None = None
    # Whether what the run is sent may go to tracing: only a company of made-up data's
    # (companies ``traces_content``). A helper agent's run follows it.
    traces: bool = False
    # The most `open` returns of one record, in characters: a helper agent reads more of it.
    open_cap: int = 12_000
    # What `open` returned, by the handle or the project's number it was opened by: what a
    # helper's quotes are checked against (ADR-0023).
    read: dict[str, str] = field(default_factory=dict)
    # Helper runs this answer started (``helpers.HELPS_AN_ANSWER``).
    helped: int = 0

    def name(self, kind: str, record_id: uuid.UUID) -> str:
        shown = self.handles.setdefault(kind, {})
        for handle, known in shown.items():
            if known == record_id:
                return f"{kind}{handle}"
        handle = len(shown) + 1
        shown[handle] = record_id
        return f"{kind}{handle}"

    def look(self, kind: str, handle: int) -> uuid.UUID | None:
        return self.handles.get(kind, {}).get(handle)
