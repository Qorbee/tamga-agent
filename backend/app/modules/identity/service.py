"""Sign-in, sessions, invites and guest sandboxes (ADR-0004).

A session is a random token whose SHA-256 is stored; the browser holds the token in an HttpOnly
cookie the router sets. Every request resolves it to an ``Actor``: the user, the company the session
acts for, and the role their membership gives them there, read fresh each time.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.companies.schemas import CompanySummary
from app.modules.identity import passwords, repository
from app.modules.identity.models import Invite, User, UserSession
from app.modules.identity.schemas import (
    InviteCreated,
    InvitePreview,
    Me,
    MembershipOut,
    UserOut,
)
from app.modules.playground import service as playground
from app.modules.playground.schemas import Choices
from app.modules.settlement import service as settlement
from app.platform import clock, storage
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import (
    BadRequest,
    Conflict,
    Gone,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    TooManyRequests,
)
from app.platform.language import Language
from app.platform.ratelimit import RateLimiter
from app.platform.tokens import hash_token, new_token

SESSION_COOKIE: Final = "tamga_session"

# last_seen_at is written at most this often, so reading a page is not a write on every request.
_TOUCH_AFTER: Final = timedelta(minutes=10)

# Deliberately loose: the invite link is what proves the address works.
_EMAIL: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class InvalidCredentials(NotAuthenticated):
    code = "invalid_credentials"


@dataclass(frozen=True, slots=True)
class OpenedSession:
    """A new session: the token goes into the cookie once and is never readable again."""

    token: str
    expires_at: datetime
    me: Me


def normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise BadRequest("Enter an email address", field="email")
    return email


async def sign_in(db: AsyncSession, *, email: str, password: str, client: str) -> OpenedSession:
    _limit_password_attempts(client)
    user = await repository.get_user_by_email(db, normalise_email(email))
    password_hash = user.password_hash if user is not None else None
    # Verified before looking at whether the account exists: an unknown email then costs the same
    # hash as a wrong password, so the response time tells nothing.
    matches = await passwords.verify_password(password_hash, password)
    if user is None or not matches:
        raise InvalidCredentials("Wrong email or password")
    memberships = await repository.get_memberships(db, user.id)
    if not memberships:
        raise PermissionDenied("This account belongs to no company")
    if password_hash is not None and passwords.needs_rehash(password_hash):
        user.password_hash = await passwords.hash_password(password)

    expires_at = utcnow() + timedelta(days=get_settings().session_days)
    opened = await _open_session(db, user, memberships[0].company_id, expires_at)
    await db.commit()
    return opened


async def resolve_actor(db: AsyncSession, token: str) -> Actor:
    now = utcnow()
    found = await repository.get_live_session(db, hash_token(token), now)
    if found is None:
        raise NotAuthenticated("Sign in again")
    session, role = found
    if now - session.last_seen_at > _TOUCH_AFTER:
        session.last_seen_at = now
        await db.commit()
    return Actor(
        user_id=session.user_id, company_id=session.company_id, role=role, session_id=session.id
    )


async def switch_company(db: AsyncSession, actor: Actor, company_id: uuid.UUID) -> Me:
    """Makes this session act for another company the user belongs to."""
    actor.require_person()
    memberships = await repository.get_memberships(db, actor.user_id)
    if company_id not in {m.company_id for m in memberships}:
        raise NotFound("You are not a member of that company")
    await repository.set_session_company(db, actor.session_id, company_id)
    await db.commit()
    user = await repository.get_user(db, actor.user_id)
    if user is None:
        raise NotAuthenticated("Sign in again")
    return await _describe(db, user, company_id)


async def sign_out(db: AsyncSession, actor: Actor) -> None:
    actor.require_person()
    await repository.delete_session(db, actor.session_id)
    await db.commit()


async def get_me(db: AsyncSession, actor: Actor) -> Me:
    user = await repository.get_user(db, actor.user_id)
    if user is None:  # deleted between resolving the session and now
        raise NotAuthenticated("Sign in again")
    return await _describe(db, user, actor.company_id)


async def update_company(db: AsyncSession, actor: Actor, *, language: Language) -> Me:
    """The owner's settings for the company this session acts for."""
    actor.require(Role.OWNER)
    await companies.set_language(db, actor.company_id, language)
    await db.commit()
    return await get_me(db, actor)


async def create_guest_session(
    db: AsyncSession,
    *,
    client: str,
    scenario: str | None = None,
    name: str | None = None,
    company_name: str | None = None,
    choices: Choices | None = None,
) -> OpenedSession:
    """A judge's one click: a sandbox company, a guest who owns it, and a session (ADR-0004).
    The company and the guest carry the names the judge gave, else the scenario's own."""
    settings = get_settings()
    chosen = playground.chosen(scenario)
    named_company, named_owner = playground.names(chosen)
    if not _guest_limiter().allow(client):
        raise TooManyRequests("Too many new sandboxes from this address. Try again later")
    now = utcnow()
    # The count holds a lock until the commit below, so the cap is exact under a burst.
    if await companies.count_live_sandboxes(db, now) >= settings.max_active_sandboxes:
        raise TooManyRequests("The playground is full. Try again later")

    expires_at = now + timedelta(hours=settings.guest_hours)
    company = await companies.create_company(
        db,
        name=(company_name or "").strip() or named_company,
        kind=CompanyKind.SANDBOX,
        expires_at=expires_at,
    )
    user = await repository.add_user(
        db, User(is_guest=True, name=(name or "").strip() or named_owner)
    )
    await repository.add_membership(db, user_id=user.id, company_id=company.id, role=Role.OWNER)
    opened = await _open_session(db, user, company.id, expires_at)
    await playground.seed_later(db, company.id, chosen, choices)
    await db.commit()
    return opened


async def create_invite(db: AsyncSession, actor: Actor, *, email: str, role: Role) -> InviteCreated:
    actor.require(Role.OWNER)
    company = (await companies.get_companies(db, [actor.company_id]))[actor.company_id]
    if company.kind is CompanyKind.SANDBOX:
        # A guest owns their sandbox; an invite would give someone a lasting account in it.
        raise PermissionDenied("A sandbox cannot invite people")
    token, invite = await _issue_invite(
        db,
        company_id=actor.company_id,
        email=normalise_email(email),
        role=role,
        created_by=actor.user_id,
    )
    await db.commit()
    return InviteCreated(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        url=invite_url(token),
    )


@dataclass(frozen=True, slots=True)
class BootstrappedCompany:
    company_id: uuid.UUID
    invite_url: str


async def bootstrap_company(
    db: AsyncSession, *, name: str, kind: CompanyKind, owner_email: str
) -> BootstrappedCompany:
    """A new company and its first owner's invite link, for the command line: nobody is signed in
    yet to invite them."""
    company = await companies.create_company(db, name=name, kind=kind)
    token, _ = await _issue_invite(
        db, company_id=company.id, email=normalise_email(owner_email), role=Role.OWNER
    )
    await db.commit()
    return BootstrappedCompany(company_id=company.id, invite_url=invite_url(token))


async def preview_invite(db: AsyncSession, token: str) -> InvitePreview:
    invite = await _live_invite(db, token)
    company = (await companies.get_companies(db, [invite.company_id]))[invite.company_id]
    existing = await repository.get_user_by_email(db, invite.email)
    return InvitePreview(
        company_name=company.name,
        email=invite.email,
        role=invite.role,
        has_account=existing is not None,
    )


async def accept_invite(
    db: AsyncSession, *, token: str, password: str, name: str | None, client: str
) -> OpenedSession:
    # Accepting checks an existing account's password, so it counts as a password attempt.
    _limit_password_attempts(client)
    now = utcnow()
    invite = await repository.claim_invite(db, hash_token(token), now)
    if invite is None:
        await _live_invite(db, token)  # raises the reason: unknown, or spent or expired
        raise Gone("This invite was just used")

    # Any failure below raises before the commit, which rolls the claim back: the invite stays
    # usable.
    user = await repository.get_user_by_email(db, invite.email)
    if user is None:
        user = await repository.insert_user_unless_email_taken(
            db,
            email=invite.email,
            name=name,
            password_hash=await passwords.hash_password(password),
        )
        if user is None:
            raise Conflict("An account with this email was just created. Sign in instead")
    elif not await passwords.verify_password(user.password_hash, password):
        raise InvalidCredentials("Enter the password of your existing account")

    await repository.add_membership(
        db, user_id=user.id, company_id=invite.company_id, role=invite.role
    )
    expires_at = now + timedelta(days=get_settings().session_days)
    opened = await _open_session(db, user, invite.company_id, expires_at)
    await db.commit()
    return opened


async def expire_sandboxes(*, batch: int = 50) -> int:
    """Deletes guest sandboxes past their lifetime: their stored files, the company with every
    row it owns, and their guests. Returns how many sandboxes went. What is left of a sandbox's
    test USDC on the vault goes back to the treasury first, and files before rows: a failure in
    either leaves the company for the next run, never USDC or files nothing points at."""
    async with open_session() as db:
        expired = await companies.expired_sandboxes(db, utcnow(), limit=batch)
    deleted = 0
    for company_id in expired:
        if not await settlement.release_remainder(company_id):
            continue
        await storage.delete_prefix(f"{company_id}/")
        async with open_session() as db:
            await companies.delete_sandbox(db, company_id)
            await db.commit()
        deleted += 1
    async with open_session() as db:
        await repository.delete_orphan_guests(db)
        await db.commit()
    return deleted


def invite_url(token: str) -> str:
    # The token rides in the fragment, which browsers never send to a server (ADR-0011).
    return f"{get_settings().web_url.rstrip('/')}/invite#{token}"


async def _open_session(
    db: AsyncSession, user: User, company_id: uuid.UUID, expires_at: datetime
) -> OpenedSession:
    token, token_hash = new_token()
    await repository.add_session(
        db,
        UserSession(
            token_hash=token_hash,
            user_id=user.id,
            company_id=company_id,
            expires_at=expires_at,
            last_seen_at=utcnow(),
        ),
    )
    return OpenedSession(
        token=token, expires_at=expires_at, me=await _describe(db, user, company_id)
    )


async def _describe(db: AsyncSession, user: User, company_id: uuid.UUID) -> Me:
    memberships = await repository.get_memberships(db, user.id)
    by_id = await companies.get_companies(db, [m.company_id for m in memberships])
    listed = [
        MembershipOut(company=CompanySummary.model_validate(by_id[m.company_id]), role=m.role)
        for m in memberships
    ]
    current = next((m for m in listed if m.company.id == company_id), None)
    if current is None:  # the membership was removed after the session was opened
        raise NotAuthenticated("Sign in again")
    return Me(
        user=UserOut.model_validate(user),
        company=current.company,
        role=current.role,
        memberships=listed,
        today=clock.now(by_id[company_id]).date(),
    )


async def _issue_invite(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    email: str,
    role: Role,
    created_by: uuid.UUID | None = None,
) -> tuple[str, Invite]:
    token, token_hash = new_token()
    invite = await repository.add_invite(
        db,
        Invite(
            company_id=company_id,
            email=email,
            role=role,
            token_hash=token_hash,
            created_by=created_by,
            expires_at=utcnow() + timedelta(days=get_settings().invite_days),
        ),
    )
    return token, invite


async def _live_invite(db: AsyncSession, token: str) -> Invite:
    invite = await repository.get_invite_by_hash(db, hash_token(token))
    if invite is None:
        raise NotFound("No such invite")
    if invite.accepted_at is not None or invite.expires_at <= utcnow():
        raise Gone("This invite was used or has expired. Ask for a new one")
    return invite


def _limit_password_attempts(client: str) -> None:
    if not _password_limiter().allow(client):
        raise TooManyRequests("Too many attempts from this address. Try again later")


@cache
def _password_limiter() -> RateLimiter:
    return RateLimiter(get_settings().password_attempts_per_client_per_hour, window_seconds=3600)


@cache
def _guest_limiter() -> RateLimiter:
    return RateLimiter(get_settings().guest_sessions_per_client_per_hour, window_seconds=3600)
