"""Password hashing with argon2id (ADR-0004), off the event loop.

A hash costs tens of milliseconds and 64 MiB by design. Every call runs in a small pool of its own:
on the loop it would stall every other request, and in the shared default pool a burst of sign-ins
would take every thread and a gigabyte of memory. Past the pool's size, hashes wait their turn.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="argon2")

# Checked when there is no account, so a wrong email costs as long as a wrong password and the
# response time does not reveal which emails have accounts.
_NO_ACCOUNT_HASH = _hasher.hash("no account has this password")


async def hash_password(password: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(_POOL, _hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """False without an account too, after the same work, so timing tells nothing."""
    matches = await asyncio.get_running_loop().run_in_executor(
        _POOL, _verify, password_hash or _NO_ACCOUNT_HASH, password
    )
    return matches and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False
