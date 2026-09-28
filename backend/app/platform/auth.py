"""Who is acting, and for which company.

The identity module resolves the session cookie into an ``Actor`` (its ``ActorDep``); every other
service takes ``company_id`` and the role from it, never from a request body or a path alone
(ADR-0003). Roles are checked in services, where the rule lives, not in routers.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.platform.errors import PermissionDenied


class Role(StrEnum):
    OWNER = "owner"  # approves payments, edits the policy, sends to clients, invites people
    OPS = "ops"  # uploads, drafts, asks
    VIEWER = "viewer"  # reads


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID
    company_id: uuid.UUID
    role: Role
    session_id: uuid.UUID

    def require(self, *roles: Role) -> None:
        if self.role not in roles:
            raise PermissionDenied(f"Needs the {' or '.join(roles)} role")
