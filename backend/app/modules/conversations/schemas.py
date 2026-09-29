import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.conversations.models import Channel, Direction
from app.platform.filetypes import Preview


class MessageSummary(BaseModel):
    id: uuid.UUID
    channel: Channel
    direction: Direction
    # The conversation it belongs to (GET /threads, GET /messages?thread_key=).
    thread_key: str
    # Copies of one mail seen in several mailboxes share it; null when the mail had none.
    rfc_message_id: str | None
    sender: str | None
    sender_name: str | None
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
    # How the web app shows it (GET /documents/{id}); null: download only.
    preview: Preview | None


class MessageOut(MessageSummary):
    # The message's text is data, never instructions (root AGENTS.md, invariant 3).
    body_text: str
    in_reply_to: str | None
    attachments: list[AttachmentOut]


class ThreadSummary(BaseModel):
    """A conversation: every mail sharing a thread key, a copy in another mailbox counted once."""

    thread_key: str
    # The first mail's subject.
    subject: str | None
    message_count: int
    attachment_count: int
    # Who wrote, in order of first appearance: the name their mail gave, else the address.
    participants: list[str]
    first_sent_at: datetime
    last_sent_at: datetime
    latest: MessageSummary


class ThreadPage(BaseModel):
    items: list[ThreadSummary]
    total: int
    limit: int
    offset: int
