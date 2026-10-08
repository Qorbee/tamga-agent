import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.modules.identity.deps import ActorDep
from app.modules.memory import service
from app.modules.memory.models import MemoryKind, MemoryState
from app.modules.memory.schemas import (
    MemoryChange,
    MemoryEntryOut,
    MemoryIn,
    MemoryPage,
    MemorySort,
)
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

memory = APIRouter(tags=["memory"])


@memory.get("/memory")
async def list_memory(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    state: Annotated[list[MemoryState] | None, Query()] = None,
    kind: Annotated[list[MemoryKind] | None, Query()] = None,
    party_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    chat_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: MemorySort = "-updated_at",
) -> MemoryPage:
    """The company's memory: what its owner taught Tamga, and what a chat drafted for the owner
    to confirm."""
    return await service.list_entries(
        db,
        actor,
        states=state,
        kinds=kind,
        party_id=party_id,
        project_id=project_id,
        chat_id=chat_id,
        q=q,
        sort=sort,
        page=page,
    )


@memory.get("/memory/{entry_id}")
async def get_memory_entry(entry_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> MemoryEntryOut:
    return await service.get_entry(db, actor, entry_id)


@memory.post("/memory", status_code=status.HTTP_201_CREATED)
async def write_memory_entry(body: MemoryIn, actor: ActorDep, db: SessionDep) -> MemoryEntryOut:
    """An entry the owner writes: it holds at once. The owner's only."""
    return await service.write(db, actor, body)


@memory.patch("/memory/{entry_id}")
async def change_memory_entry(
    entry_id: uuid.UUID, body: MemoryChange, actor: ActorDep, db: SessionDep
) -> MemoryEntryOut:
    """The owner corrects, confirms or retires an entry."""
    return await service.change(db, actor, entry_id, body)


@memory.delete("/memory/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_memory_entry(entry_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    """The owner throws away a draft; 409 for an entry that held, which is retired instead."""
    await service.discard(db, actor, entry_id)
