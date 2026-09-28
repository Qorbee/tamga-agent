"""``ActorDep``: who is acting, for which company. The one thing other modules' routers import from
identity; their services then take the company from the ``Actor``, never from the request."""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyCookie

from app.modules.identity import service
from app.modules.identity.service import SESSION_COOKIE
from app.platform.auth import Actor
from app.platform.db import SessionDep
from app.platform.errors import NotAuthenticated

_session_cookie = APIKeyCookie(
    name=SESSION_COOKIE,
    auto_error=False,
    description="Set by signing in, accepting an invite or opening a guest sandbox.",
)


async def current_actor(
    db: SessionDep, token: Annotated[str | None, Security(_session_cookie)]
) -> Actor:
    if not token:
        raise NotAuthenticated("Sign in first")
    return await service.resolve_actor(db, token)


ActorDep = Annotated[Actor, Depends(current_actor)]
