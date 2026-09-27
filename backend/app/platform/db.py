"""The PostgreSQL engine, the request session, and the declarative base every model subclasses.

The engine is created in the app's lifespan and disposed on shutdown. A request gets one session
through ``SessionDep``; the service layer owns the transaction and commits it — the dependency
never commits on its own, so a request that raises leaves nothing half-written.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import Depends
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# The names PostgreSQL would choose on its own. Without a convention SQLAlchemy leaves constraints
# unnamed, autogenerate emits `drop_constraint(None, ...)` in downgrades, and every one needs a hand
# fix. CHECK constraints are left out: name those explicitly.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def init_db(url: str) -> None:
    global _engine, _sessions
    _engine = create_async_engine(url, pool_pre_ping=True)
    # expire_on_commit=False: a service can return an object it just committed without a lazy
    # reload, which would be blocking I/O outside the session in async code.
    _sessions = async_sessionmaker(_engine, expire_on_commit=False)


async def dispose_db() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessions = None, None


async def get_session() -> AsyncIterator[AsyncSession]:
    if _sessions is None:
        raise RuntimeError("init_db() has not run; the app lifespan opens the database")
    async with _sessions() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
