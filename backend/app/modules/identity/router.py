from fastapi import APIRouter, Request, Response, status

from app.config import get_settings
from app.modules.companies.schemas import CompanyUpdate
from app.modules.identity import service
from app.modules.identity.deps import ActorDep
from app.modules.identity.schemas import (
    CompanySwitch,
    GuestStart,
    InviteAccept,
    InviteCreate,
    InviteCreated,
    InvitePreview,
    InviteToken,
    Me,
    SignIn,
)
from app.modules.identity.service import SESSION_COOKIE, OpenedSession
from app.platform.db import SessionDep

sessions = APIRouter(tags=["sessions"])
me = APIRouter(tags=["me"])
invites = APIRouter(tags=["invites"])


def _set_session_cookie(response: Response, opened: OpenedSession) -> None:
    # HttpOnly: page scripts never see it. Lax: not sent on cross-site POSTs, which with the origin
    # check covers CSRF (ADR-0004). Secure: browsers still accept it on http://localhost.
    response.set_cookie(
        SESSION_COOKIE,
        opened.token,
        expires=opened.expires_at,
        path="/",
        domain=get_settings().session_cookie_domain or None,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _client(request: Request) -> str:
    # Behind the proxy this is the real client only when uvicorn trusts the proxy's
    # X-Forwarded-For (FORWARDED_ALLOW_IPS in .env.example).
    return request.client.host if request.client else "unknown"


@sessions.post("/sessions")
async def sign_in(body: SignIn, request: Request, db: SessionDep, response: Response) -> Me:
    opened = await service.sign_in(
        db,
        email=body.email,
        password=body.password.get_secret_value(),
        client=_client(request),
    )
    _set_session_cookie(response, opened)
    return opened.me


@sessions.post("/sessions/guest", status_code=status.HTTP_201_CREATED)
async def create_guest_session(
    request: Request, db: SessionDep, response: Response, body: GuestStart | None = None
) -> Me:
    opened = await service.create_guest_session(
        db, client=_client(request), scenario=body.scenario if body else None
    )
    _set_session_cookie(response, opened)
    return opened.me


@sessions.put("/sessions/current/company")
async def switch_company(body: CompanySwitch, actor: ActorDep, db: SessionDep) -> Me:
    return await service.switch_company(db, actor, body.company_id)


@sessions.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(actor: ActorDep, db: SessionDep, response: Response) -> None:
    await service.sign_out(db, actor)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        domain=get_settings().session_cookie_domain or None,
        secure=True,
        httponly=True,
        samesite="lax",
    )


@me.get("/me")
async def get_me(actor: ActorDep, db: SessionDep) -> Me:
    return await service.get_me(db, actor)


@me.patch("/me/company")
async def update_company(body: CompanyUpdate, actor: ActorDep, db: SessionDep) -> Me:
    return await service.update_company(db, actor, language=body.language)


@invites.post("/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(body: InviteCreate, actor: ActorDep, db: SessionDep) -> InviteCreated:
    return await service.create_invite(db, actor, email=body.email, role=body.role)


@invites.post("/invites/lookup")
async def preview_invite(body: InviteToken, db: SessionDep) -> InvitePreview:
    return await service.preview_invite(db, body.token)


@invites.post("/invites/accept")
async def accept_invite(
    body: InviteAccept, request: Request, db: SessionDep, response: Response
) -> Me:
    opened = await service.accept_invite(
        db,
        token=body.token,
        password=body.password.get_secret_value(),
        name=body.name,
        client=_client(request),
    )
    _set_session_cookie(response, opened)
    return opened.me
