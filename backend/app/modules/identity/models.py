import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.auth import Role
from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # A guest has neither: it exists only inside its sandbox, and cannot sign in again.
        CheckConstraint(
            "is_guest OR (email IS NOT NULL AND password_hash IS NOT NULL)",
            name="users_email_and_password_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # Lower-cased by the service, so the unique index is case-insensitive in effect.
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(200))
    is_guest: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(text_enum(Role, "memberships_role_check"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserSession(Base):
    """A signed-in browser. Named apart from SQLAlchemy's ``Session``; the table is ``sessions``."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The company this session acts for; its role comes from the membership, read on every request,
    # so removing a membership takes effect at once.
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class Invite(CompanyScoped, Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[Role] = mapped_column(text_enum(Role, "invites_role_check"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    # Null for the first owner's invite, which a command line creates with the company.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
