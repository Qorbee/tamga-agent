"""Readers the isolation tests run: each misbehaves the way a crafted file can make a library."""

import os
import time

from app.platform.isolated import Json, Reason, Refused, register


@register("test.echo")
def echo(data: bytes, hint: str) -> Json:
    return {"size": len(data), "hint": hint}


@register("test.hog")
def hog(data: bytes, hint: str) -> Json:
    held = bytearray(2 << 30)
    return len(held)


@register("test.spin")
def spin(data: bytes, hint: str) -> Json:
    while True:
        pass


@register("test.sleep")
def sleep(data: bytes, hint: str) -> Json:
    # The hint is how long, in seconds; half a minute unless the test says.
    time.sleep(float(hint or 30))
    return None


@register("test.refuse")
def refuse(data: bytes, hint: str) -> Json:
    raise Refused(Reason.PASSWORD)


@register("test.crash")
def crash(data: bytes, hint: str) -> Json:
    raise ValueError("a library gave up")


@register("test.write")
def write(data: bytes, hint: str) -> Json:
    # The hint is a path the test chose.
    with open(hint, "wb") as file:
        file.write(b"x" * 1024)
    return "written"


@register("test.read")
def read(data: bytes, hint: str) -> Json:
    # The hint is a path the test chose.
    with open(hint, "rb") as file:
        return len(file.read())


@register("test.not_json")
def not_json(data: bytes, hint: str) -> Json:
    return {"value": object()}  # type: ignore[dict-item]  # a reader bug, sent as nothing


@register("test.environment")
def environment(data: bytes, hint: str) -> Json:
    with open("/proc/self/environ", "rb") as file:
        started_with = [
            entry.split(b"=")[0].decode() for entry in file.read().split(b"\0") if entry
        ]
    now: list[Json] = [name for name in sorted(os.environ)]
    at_start: list[Json] = [name for name in sorted(started_with)]
    return {"now": now, "at_start": at_start}
