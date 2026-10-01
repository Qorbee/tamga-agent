import logging

import pytest

from app.modules.books import service, tasks


async def test_a_sync_that_skips_a_source_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def nothing_configured() -> service.SyncResult:
        return service.SyncResult(skipped=["ledger"])

    monkeypatch.setattr(service, "sync_pilot_books", nothing_configured)

    with caplog.at_level(logging.WARNING, logger=tasks.__name__):
        await tasks.sync_books(0)

    [record] = caplog.records
    assert "ledger" in record.getMessage() and "BOOKS_API_KEY" in record.getMessage()


async def test_a_sync_that_reads_its_sources_warns_of_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def read() -> service.SyncResult:
        return service.SyncResult(parties=2, invoices=3)

    monkeypatch.setattr(service, "sync_pilot_books", read)

    with caplog.at_level(logging.WARNING, logger=tasks.__name__):
        await tasks.sync_books(0)

    assert caplog.records == []
