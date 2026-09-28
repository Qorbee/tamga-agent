import uuid

import httpx
import pytest

from app.platform import storage

pytestmark = pytest.mark.integration


async def test_a_file_round_trips_by_its_content_key(storage_bucket: None) -> None:
    company = uuid.uuid4()
    data = b"%PDF-1.7 a CMR"
    key = storage.content_key(company, data)

    await storage.put(key, data, content_type="application/pdf")

    assert key == storage.content_key(company, data)  # the same bytes, the same object
    assert key.startswith(f"{company}/")
    assert await storage.get(key) == data
    # The URL a browser gets; the test runs on the host, so it can read it the same way.
    async with httpx.AsyncClient() as client:
        response = await client.get(storage.presign(key, filename="CMR 34 ABC 12.pdf"))
    assert response.content == data
    assert "CMR%2034%20ABC%2012.pdf" in response.headers["content-disposition"]

    await storage.delete(key)


async def test_storage_reports_ready_when_its_bucket_answers(storage_bucket: None) -> None:
    assert await storage.ready() is True
