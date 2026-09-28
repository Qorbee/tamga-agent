"""The channel adapter interface, and the one place sending is allowed or refused.

``ChannelAdapter.send`` checks the company's mode before anything leaves: only a ``live`` company
sends (ADR-0009, root AGENTS.md invariant 6). Adapters implement ``_deliver`` and never override
``send``, so no adapter can skip the check, whatever called it.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.conversations.models import Channel
from app.platform.errors import PermissionDenied


class ModeForbidsSending(PermissionDenied):
    code = "mode_forbids_sending"


@dataclass(frozen=True, slots=True)
class Outbound:
    to: str
    text: str
    subject: str | None = None


class ChannelAdapter(ABC):
    channel: ClassVar[Channel]

    @final
    async def send(self, db: AsyncSession, company_id: uuid.UUID, outbound: Outbound) -> None:
        company = (await companies.get_companies(db, [company_id]))[company_id]
        if company.mode is not CompanyMode.LIVE:
            raise ModeForbidsSending(
                f"A company in {company.mode} mode sends nothing; the owner sends drafts themselves"
            )
        await self._deliver(outbound)

    @abstractmethod
    async def _deliver(self, outbound: Outbound) -> None: ...
