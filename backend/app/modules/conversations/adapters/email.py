from app.modules.conversations.adapters.base import ChannelAdapter, Outbound
from app.modules.conversations.models import Channel


class EmailAdapter(ChannelAdapter):
    """Mail comes in over IMAP (``imap.py``, polled by ``tasks.py``). Nothing goes out: the owner
    sends drafts from their own mail client, and SMTP is added only for a ``live`` company."""

    channel = Channel.EMAIL

    async def _deliver(self, outbound: Outbound) -> None:
        raise NotImplementedError("mail is read-only (ADR-0008)")
