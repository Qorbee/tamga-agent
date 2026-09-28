from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.platform.clock import now, utcnow


@dataclass
class Company:
    clock_offset: timedelta = timedelta(0)
    replay_cursor: datetime | None = None


def test_a_pilot_runs_on_the_wall_clock() -> None:
    before = utcnow()

    assert before <= now(Company()) <= utcnow()


def test_a_sandbox_runs_ahead_by_its_offset() -> None:
    two_days = timedelta(days=2)

    assert now(Company(clock_offset=two_days)) - utcnow() > two_days - timedelta(seconds=5)


def test_a_replay_is_at_its_cursor_and_never_past_it() -> None:
    cursor = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    assert now(Company(replay_cursor=cursor)) == cursor


def test_times_carry_their_zone() -> None:
    assert utcnow().tzinfo is UTC
