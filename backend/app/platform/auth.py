"""Who is acting, and for which company.

The identity module resolves the session cookie into an ``Actor`` (its ``ActorDep``); every other
service takes ``company_id`` and the role from it, never from a request body or a path alone
(ADR-0003). Roles are checked in services, where the rule lives, not in routers.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.platform.errors import PermissionDenied


class Role(StrEnum):
    OWNER = "owner"  # approves payments, edits the policy, sends to clients, invites people
    OPS = "ops"  # uploads, drafts, asks
    VIEWER = "viewer"  # reads


# The user and the session of an actor that has neither.
_NOBODY: Final = uuid.UUID(int=0)


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID
    company_id: uuid.UUID
    role: Role
    session_id: uuid.UUID
    # Tamga itself, working for the company with nobody signed in (ADR-0018): it reads what a
    # viewer reads, and no role check passes it, whatever role the check names.
    background: bool = False

    @classmethod
    def of_tamga(cls, company_id: uuid.UUID) -> Actor:
        """The actor a turn Tamga takes on its own runs as: the company's, with no user and no
        session. Nothing it does is an owner's act."""
        return cls(
            user_id=_NOBODY,
            company_id=company_id,
            role=Role.VIEWER,
            session_id=_NOBODY,
            background=True,
        )

    def require(self, *roles: Role) -> None:
        if self.background or self.role not in roles:
            raise PermissionDenied(f"Needs the {' or '.join(roles)} role")

    def require_person(self) -> None:
        """For what any signed-in user may do, whatever their role, and Tamga may not."""
        if self.background:
            raise PermissionDenied("Needs a signed-in user")
