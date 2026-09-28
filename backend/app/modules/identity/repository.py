import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import Invite, Membership, User, UserSession
from app.platform.auth import Role


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Bound to a name first: returned directly, mypy infers the overload from the return type and
    # falls back to Any.
    user = await db.scalar(select(User).where(User.email == email))
    return user


async def add_user(db: AsyncSession, user: User) -> User:
    db.add(user)
    await db.flush()
    return user


async def insert_user_unless_email_taken(
    db: AsyncSession, *, email: str, name: str | None, password_hash: str
) -> User | None:
    """None when the email already has an account, including one created a moment ago by a
    concurrent request: the unique index decides, not an earlier read."""
    return await db.scalar(
        insert(User)
        .values(email=email, name=name, password_hash=password_hash)
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User)
    )


async def get_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[Membership]:
    rows = await db.scalars(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
    )
    return list(rows)


async def add_membership(
    db: AsyncSession, *, user_id: uuid.UUID, company_id: uuid.UUID, role: Role
) -> None:
    """Keeps an existing membership as it is: an invite never changes a member's role silently."""
    await db.execute(
        insert(Membership)
        .values(user_id=user_id, company_id=company_id, role=role)
        .on_conflict_do_nothing(index_elements=[Membership.user_id, Membership.company_id])
    )


async def add_session(db: AsyncSession, session: UserSession) -> UserSession:
    db.add(session)
    await db.flush()
    return session


async def get_live_session(
    db: AsyncSession, token_hash: bytes, now: datetime
) -> tuple[UserSession, Role] | None:
    """The session and the role its user holds in the session's company, if both still exist."""
    row = (
        await db.execute(
            select(UserSession, Membership.role)
            .join(
                Membership,
                (Membership.user_id == UserSession.user_id)
                & (Membership.company_id == UserSession.company_id),
            )
            .where(UserSession.token_hash == token_hash, UserSession.expires_at > now)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def set_session_company(
    db: AsyncSession, session_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    await db.execute(
        update(UserSession).where(UserSession.id == session_id).values(company_id=company_id)
    )


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(delete(UserSession).where(UserSession.id == session_id))


async def add_invite(db: AsyncSession, invite: Invite) -> Invite:
    db.add(invite)
    await db.flush()
    return invite


# The two invite reads below take no company_id: the token is the capability, and the company is
# what it resolves to (ADR-0003's rule applies once the holder is known).
async def get_invite_by_hash(db: AsyncSession, token_hash: bytes) -> Invite | None:
    invite = await db.scalar(select(Invite).where(Invite.token_hash == token_hash))
    return invite


async def claim_invite(db: AsyncSession, token_hash: bytes, now: datetime) -> Invite | None:
    """Marks a live invite accepted in one statement, so two tabs accepting at once cannot both
    succeed. None when it is unknown, spent or expired."""
    return await db.scalar(
        update(Invite)
        .where(
            Invite.token_hash == token_hash,
            Invite.accepted_at.is_(None),
            Invite.expires_at > now,
        )
        .values(accepted_at=now)
        .returning(Invite)
    )


async def delete_orphan_guests(db: AsyncSession) -> int:
    """Guests whose sandbox is gone: a guest exists only inside its sandbox."""
    result = await db.execute(
        delete(User)
        .where(
            User.is_guest.is_(True),
            ~select(Membership.id).where(Membership.user_id == User.id).exists(),
        )
        .returning(User.id)
    )
    return len(result.all())
