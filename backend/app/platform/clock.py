"""Time. Modules never call ``datetime.now()`` or ``time.time()`` themselves.

- ``now(company)`` is **business time**, what a rule, a timer or a reminder means by "today". A
  sandbox's clock runs ahead of the wall clock by its offset ("skip two days"); a replay's clock is
  its cursor, which never passes the history fed so far (ADR-0009).
- ``utcnow()`` is the wall clock, for what runs on real time whatever the company: sessions,
  invites, rate limits.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class CompanyClock(Protocol):
    """What ``now`` needs from a company; the ``companies`` model has both columns."""

    @property
    def clock_offset(self) -> timedelta: ...

    @property
    def replay_cursor(self) -> datetime | None: ...


def now(company: CompanyClock) -> datetime:
    if company.replay_cursor is not None:
        return company.replay_cursor
    return utcnow() + company.clock_offset


def utcnow() -> datetime:
    return datetime.now(UTC)
