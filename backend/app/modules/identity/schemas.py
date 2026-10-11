import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.modules.companies.schemas import CompanySummary
from app.platform.auth import Role

# Long enough to resist guessing, capped so a huge body cannot make one hash expensive.
PASSWORD_MIN = 10
PASSWORD_MAX = 256


class SignIn(BaseModel):
    email: str = Field(max_length=320)
    # No minimum here: sign-in must not tell anyone what the rule for new passwords is.
    password: SecretStr = Field(max_length=PASSWORD_MAX)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    name: str | None
    is_guest: bool


class MembershipOut(BaseModel):
    company: CompanySummary
    role: Role


class Me(BaseModel):
    """Who is signed in, the company this session acts for, and every company they belong to."""

    user: UserOut
    company: CompanySummary
    role: Role
    memberships: list[MembershipOut]
    # The company's business day: a sandbox's runs ahead by every skip (app/platform/clock.py).
    today: date


class GuestStart(BaseModel):
    # A scenario's key (GET /playground/scenarios); the first registered when none is given.
    scenario: str | None = Field(default=None, max_length=40)


class CompanySwitch(BaseModel):
    company_id: uuid.UUID


class InviteCreate(BaseModel):
    email: str = Field(max_length=320)
    role: Role


class InviteCreated(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    # Shown once: only the token's hash is stored, so a lost link means a new invite.
    url: str


class InviteToken(BaseModel):
    # In the body, never the path: a token in a URL ends up in proxy logs (ADR-0011).
    token: str = Field(min_length=20, max_length=100)


class InvitePreview(BaseModel):
    company_name: str
    email: str
    role: Role
    # True when the email already has an account: the page then asks for that password instead of
    # a new one.
    has_account: bool


class InviteAccept(InviteToken):
    password: SecretStr = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    name: str | None = Field(default=None, max_length=200)
