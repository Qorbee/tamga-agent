"""Time. Modules never call ``datetime.now()`` or ``time.time()`` themselves.

``utcnow()`` is the wall clock, for what runs on real time whatever the company: sessions, invites,
rate limits. Business time — a sandbox's simulated clock, a replay's cursor — is a different
question with a company in it (ADR-0009), and never this function.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
