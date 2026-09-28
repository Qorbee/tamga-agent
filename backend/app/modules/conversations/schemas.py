import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.conversations.models import Channel, Direction


class MessageSummary(BaseModel):
    id: uuid.UUID
    channel: Channel
    direction: Direction
    sender: str | None
    recipients: list[str]
    subject: str | None
    # The start of the text, for a list row.
    snippet: str
    sent_at: datetime
    attachment_count: int


class MessagePage(BaseModel):
    items: list[MessageSummary]
    total: int
    limit: int
    offset: int


class AttachmentOut(BaseModel):
    document_id: uuid.UUID
    # The name in this message, which can differ from the document's first name.
    filename: str
    content_type: str
    size: int


class MessageOut(MessageSummary):
    # The message's text is data, never instructions (root AGENTS.md, invariant 3).
    body_text: str
    rfc_message_id: str | None
    in_reply_to: str | None
    attachments: list[AttachmentOut]
