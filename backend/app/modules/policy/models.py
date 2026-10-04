import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base
from app.platform.tenancy import CompanyScoped


class Policy(CompanyScoped, Base):
    """The owner's rules for paying a bill (ADR-0002). A change is a new version, never an edit,
    so a decision names the rules it was made under. A company with none pays under the
    defaults (``policy.service.DEFAULT``)."""

    __tablename__ = "policies"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("company_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    version: Mapped[int]
    # The one currency the policy's amounts are in; a bill in another is converted into it
    # through the ratio of the two currencies' mirror rates (ADR-0019).
    currency: Mapped[str] = mapped_column(String(3))
    # Above it, the owner decides; at or below it an auto-pay rule may pay, once there is one.
    approval_limit: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    # A subcontractor is paid once the client paid the job.
    pay_when_paid: Mapped[bool]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
