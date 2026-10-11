import ast
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.modules.documents import reading
from app.platform import isolated
from app.platform.isolated import Limits, Outcome, Reason
from app.tests.platform import isolated_readers  # noqa: F401  (registers the test readers)

APP = Path(__file__).resolve().parents[2]
TIGHT = Limits(memory_bytes=512 << 20, cpu_seconds=1, deadline_seconds=5)


async def test_a_reader_gets_the_bytes_and_hint_and_sends_back_json() -> None:
    assert await isolated.run("test.echo", b"12345", "application/pdf") == Outcome(
        value={"size": 5, "hint": "application/pdf"}
    )


async def test_a_reader_past_its_memory_gets_limits_and_the_next_read_runs() -> None:
    assert await isolated.run("test.hog", b"", "", TIGHT) == Outcome(refused=Reason.LIMITS)
    assert (await isolated.run("test.echo", b"x", "", TIGHT)).value == {"size": 1, "hint": ""}


async def test_a_reader_that_spins_is_stopped_at_its_cpu_limit() -> None:
    assert await isolated.run("test.spin", b"", "", TIGHT) == Outcome(refused=Reason.LIMITS)


async def test_a_reader_that_hangs_is_killed_at_the_deadline() -> None:
    limits = Limits(deadline_seconds=0.5)

    assert await isolated.run("test.sleep", b"", "", limits) == Outcome(refused=Reason.LIMITS)


async def test_a_refusal_keeps_its_reason_and_any_other_failure_is_damaged() -> None:
    assert await isolated.run("test.refuse", b"", "") == Outcome(refused=Reason.PASSWORD)
    assert await isolated.run("test.crash", b"", "") == Outcome(refused=Reason.DAMAGED)
    assert await isolated.run("test.not_json", b"", "") == Outcome(refused=Reason.DAMAGED)


async def test_a_reader_cannot_write_to_a_file(tmp_path: Path) -> None:
    target = tmp_path / "written"

    assert await isolated.run("test.write", b"", str(target)) == Outcome(refused=Reason.DAMAGED)
    # Opening may create it; not one byte reaches it.
    assert target.stat().st_size == 0


async def test_a_child_holds_none_of_the_settings_secrets() -> None:
    # The suite's settings are in this process's environment (conftest.py).
    outcome = await isolated.run("test.environment", b"", "")

    assert isinstance(outcome.value, dict)
    for names in (outcome.value["now"], outcome.value["at_start"]):
        assert isinstance(names, list)
        assert not {"DATABASE_URL", "OPENAI_API_KEY", "S3_SECRET_ACCESS_KEY"} & set(names)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every process's /proc files")
async def test_a_child_cannot_read_its_parents_environment() -> None:
    assert await isolated.run("test.environment", b"", "") != Outcome(refused=Reason.DAMAGED)
    # Its own, yes; the process that holds the settings' secrets (this one), no.
    parent = f"/proc/{os.getpid()}/environ"
    assert await isolated.run("test.read", b"", parent) == Outcome(refused=Reason.DAMAGED)


async def test_reads_past_the_children_a_process_runs_wait_their_turn() -> None:
    reads = [isolated.run("test.sleep", b"", "0.3") for _ in range(isolated.MAX_CHILDREN + 1)]
    started = time.monotonic()

    await asyncio.gather(*reads)

    assert time.monotonic() - started >= 0.6


def test_a_reader_is_reached_only_through_run() -> None:
    # ADR-0012: nothing calls a registered reader, or the parser under the spreadsheet readers,
    # outside the child.
    names = {
        reader.__name__
        for reader in isolated.registered().values()
        # The tests' own readers (one is called ``sleep``) are no app reader.
        if not reader.__module__.startswith("app.tests.")
    }
    names |= {"read_workbook", "visible_text"}
    allowed = {"modules/documents/reading.py", "modules/documents/sheets.py"}
    offenders = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if relative in allowed or relative.startswith("tests/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                called = node.func
                name = (
                    called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
                )
                if name in names:
                    offenders.append(f"{relative}:{node.lineno} {name}")
    assert offenders == []
    assert {"documents.pdf_text", "documents.sheet_text", "documents.workbook"} <= set(
        isolated.registered()
    )
    assert reading.pdf_text.__module__ == "app.modules.documents.reading"


def test_the_worker_module_reads_no_settings_as_it_is_imported() -> None:
    """Each reader's child imports the worker's main module again, with none of the settings in its
    environment (ADR-0012). A module there that reads them as it is imported fails every document
    on the server, where no .env file stands in for the environment."""
    code = (
        "import app.config\n"
        "def refused():\n"
        "    raise SystemExit('the settings were read as app.worker was imported')\n"
        "app.config.get_settings = refused\n"
        "import app.worker\n"
    )
    done = subprocess.run(  # noqa: S603  (this interpreter, a fixed script)
        [sys.executable, "-c", code], cwd=APP.parent, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
