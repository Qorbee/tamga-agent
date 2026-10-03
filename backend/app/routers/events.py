"""``GET /events``: the company event stream (``app/platform/events.py``), as Server-Sent Events.

A stream is closed after a while and the browser opens it again on its own, a few seconds
later: the session is checked each time, so a browser signed out, or moved to another company,
stops hearing this one.
"""

import asyncio
import time
from collections.abc import AsyncIterable
from typing import Final

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse

from app.modules.identity.deps import StreamingActorDep
from app.platform import events

router = APIRouter(tags=["events"])

_OPEN_FOR: Final = 15 * 60.0


@router.get("/events", response_class=EventSourceResponse)
async def stream_events(actor: StreamingActorDep) -> AsyncIterable[events.CompanyEvent]:
    """What changes in the caller's company from now on: one note per kind and record, without
    its content. The browser reads what changed through the resources it already uses."""
    async with events.stream(actor.company_id) as stream:
        closes = time.monotonic() + _OPEN_FOR
        while (left := closes - time.monotonic()) > 0:
            try:
                taken = await asyncio.wait_for(stream.take(), left)
            except TimeoutError:
                return
            if stream.closed:
                return
            for event in taken:
                yield event
