"""Readers of untrusted files, each run in a locked child process (ADR-0012).

Every file comes from outside, and the libraries that read one can be made to allocate without
bound, spin, or crash. So no reader runs in the API or the worker: a forkserver imports the readers
once, and each read is a fresh child forked from it, which lowers its own limits before it touches
the bytes. A child that hits a limit or the deadline is killed; that is the file's outcome, and the
parent carries on.

Only JSON comes back, never a pickle: a child that a crafted file took over could otherwise send an
object that runs code when the parent loads it. The caller validates what it gets (a Pydantic
model), as it would any input from outside. Nor does a child hold a secret to leak, since it can
still reach the network (ADR-0012): the forkserver starts with none of the settings' environment
(the database URL, the API keys), and the process that starts it is made not dumpable, so a child
running as the same user cannot read that process's environment or memory through ``/proc``.

What the limits do not stop: a child runs as the same user as its parent, so it may delete, rename
or truncate a file that user owns (``RLIMIT_FSIZE`` 0 only refuses writing bytes). The image runs
as a user that owns none of the code or configuration (backend/Dockerfile).

At most ``MAX_CHILDREN`` children run per process at once; more reads wait their turn, so a burst of
hostile files (or preview requests) costs time, not the server's memory.

A reader is registered by name (``register``) and reached only through ``run``; a test walks the
registry to hold that.
"""

import asyncio
import ctypes
import importlib
import json
import multiprocessing
import multiprocessing.forkserver
import os
import resource
import sys
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.context import ForkServerContext
from typing import Final

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type Reader = Callable[[bytes, str], Json]


class Reason(StrEnum):
    """Why a file could not be read, as the owner is told (ADR-0012)."""

    PASSWORD = "password"  # noqa: S105  (a reason's name, not a secret)
    UNSUPPORTED = "unsupported"
    DAMAGED = "damaged"
    TOO_LARGE = "too_large"
    # The child hit a memory, CPU or time limit.
    LIMITS = "limits"


class Refused(Exception):
    """Raised by a reader for a file it cannot read, with the reason the owner is told."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Limits:
    """What one read may take (ADR-0012): address space, CPU time and wall time."""

    memory_bytes: int = 1 << 30
    cpu_seconds: int = 30
    deadline_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class Outcome:
    # What the reader returned; None when it refused.
    value: Json = None
    refused: Reason | None = None


# ADR-0012's limits, for every read but the tests'.
DEFAULT_LIMITS: Final = Limits()
# The most a reader may send back: text and rows, never the file again.
_MAX_REPLY: Final = 64 * 1024 * 1024

# The environment the forkserver, and so every child, starts with: what Python and a locale need.
_KEPT_ENVIRONMENT: Final = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR"})

# Children one process runs at once, each up to the memory limit.
MAX_CHILDREN: Final = 2
# prctl's option that makes a process's /proc files readable only by root (linux/prctl.h).
_PR_SET_DUMPABLE: Final = 4

_readers: dict[str, Reader] = {}
# One semaphore per event loop: the api and the worker run one each, the tests one per test.
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
_context: ForkServerContext | None = None
_starting = threading.Lock()


def register(name: str) -> Callable[[Reader], Reader]:
    """Makes a reader runnable. It gets the bytes and a hint (the file's media type) and returns
    JSON: numbers, text, lists and dicts."""

    def add(reader: Reader) -> Reader:
        existing = _readers.get(name)
        if existing is not None and existing is not reader:
            raise ValueError(f"reader {name!r} is already registered")
        _readers[name] = reader
        return reader

    return add


def registered() -> dict[str, Reader]:
    return dict(_readers)


async def run(name: str, data: bytes, hint: str, limits: Limits = DEFAULT_LIMITS) -> Outcome:
    """Reads ``data`` with the reader ``name`` in a child of its own."""
    reader = _readers.get(name)
    if reader is None:
        raise KeyError(f"no reader {name!r}")
    async with _slot():
        return await asyncio.to_thread(_run, name, reader.__module__, data, hint, limits)


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(MAX_CHILDREN)
    return slot


def _run(name: str, module: str, data: bytes, hint: str, limits: Limits) -> Outcome:
    context = _forkserver()
    parent, child = context.Pipe()
    process = context.Process(target=_child, args=(name, module, limits, child), daemon=True)
    process.start()
    child.close()
    try:
        parent.send_bytes(data)
        parent.send_bytes(hint.encode())
        if not parent.poll(limits.deadline_seconds):
            return Outcome(refused=Reason.LIMITS)
        reply = json.loads(parent.recv_bytes(_MAX_REPLY))
        if reply.get("refused") is not None:
            return Outcome(refused=Reason(reply["refused"]))
        value: Json = reply["value"]
        return Outcome(value=value)
    except EOFError, OSError:  # the child died: killed at its CPU limit, or out of memory
        return Outcome(refused=Reason.LIMITS)
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent.close()


def start() -> None:
    """Starts the forkserver, importing every registered reader's module once so each child starts
    warm, with none of this process's secrets in its environment. The api and the worker call it
    at startup, before other threads run: the environment is narrowed while the server starts."""
    _forkserver()


def _forkserver() -> ForkServerContext:
    global _context
    with _starting:
        if _context is None:
            _not_dumpable()
            context = multiprocessing.get_context("forkserver")
            context.set_forkserver_preload(
                sorted({reader.__module__ for reader in _readers.values()})
            )
            removed = {
                key: value for key, value in os.environ.items() if key not in _KEPT_ENVIRONMENT
            }
            for key in removed:
                del os.environ[key]
            try:
                multiprocessing.forkserver.ensure_running()
            finally:
                os.environ.update(removed)
            _context = context
    return _context


def _not_dumpable() -> None:
    """Makes this process's /proc files (its environment, its memory) readable only by root, so a
    child cannot read the secrets its parent holds. Linux only: elsewhere, a laptop's dev server."""
    if sys.platform != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


def _child(name: str, module: str, limits: Limits, connection: Connection) -> None:
    # Nothing the reader prints may reach a file: with no file writes allowed, one byte to a
    # redirected stderr would kill the child for the wrong reason.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    for limit, value in (
        (resource.RLIMIT_AS, limits.memory_bytes),
        (resource.RLIMIT_CPU, limits.cpu_seconds),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(limit, (value, value))
    data = connection.recv_bytes()
    hint = connection.recv_bytes().decode()
    try:
        # A reader registered after the forkserver started is imported here.
        importlib.import_module(module)
        reply = json.dumps({"value": _readers[name](data, hint)})
    except Refused as error:
        reply = json.dumps({"refused": error.reason.value})
    except MemoryError:
        reply = json.dumps({"refused": Reason.LIMITS.value})
    except Exception:  # a hostile or broken file fails a library in many ways
        reply = json.dumps({"refused": Reason.DAMAGED.value})
    connection.send_bytes(reply.encode())
    connection.close()
