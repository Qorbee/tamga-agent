"""Who a run of the assistant answers, and the handles it gave what it showed."""

import uuid
from dataclasses import dataclass, field

from app.modules.linking import service as linking
from app.platform.auth import Actor


@dataclass
class Seat(linking.Desk):
    """Who a run of the assistant answers, on top of what any reading run keeps of what it was
    shown: its tools act as this user, with this user's role, and never as anyone else. A file,
    a link or a truck is named to the model by a handle the run gave it (f2, l3, t1), so a tool
    can only reach what the run was shown."""

    actor: Actor = field(kw_only=True)
    chat_id: uuid.UUID = field(kw_only=True)
    # Kind ("f", "l", "t") → handle → id, in the order they were shown.
    handles: dict[str, dict[int, uuid.UUID]] = field(default_factory=dict)

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
