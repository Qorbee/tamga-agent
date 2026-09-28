import httpx
import pytest

from app.config import get_settings
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def test_an_upload_is_stored_once_and_typed(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    params = {"filename": "CMR 34 ABC 12.pdf", "doc_type": "cmr", "note": "from the driver"}
    headers = {"content-type": "application/pdf"}

    first = await client.post("/documents", params=params, content=b"%PDF-1.7 a", headers=headers)
    again = await client.post(
        "/documents", params={"filename": "copy.pdf"}, content=b"%PDF-1.7 a", headers=headers
    )
    listed = await client.get("/documents")
    detail = await client.get(f"/documents/{first.json()['document']['id']}")

    assert first.status_code == 201 and first.json()["new_document"] is True
    assert again.json()["new_document"] is False
    assert again.json()["document"]["id"] == first.json()["document"]["id"]
    assert listed.json()["total"] == 1
    document = detail.json()
    assert document["doc_type"] == "cmr" and document["content_type"] == "application/pdf"
    async with httpx.AsyncClient() as browser:
        assert (await browser.get(document["download_url"])).content == b"%PDF-1.7 a"


async def test_an_unknown_type_an_empty_file_and_a_huge_one_are_refused(
    client: httpx.AsyncClient, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await guest_client(client)
    monkeypatch.setattr(get_settings(), "upload_max_bytes", 10)

    unknown = await client.post(
        "/documents", params={"filename": "a", "doc_type": "passport"}, content=b"x"
    )
    empty = await client.post("/documents", params={"filename": "a"}, content=b"")
    huge = await client.post("/documents", params={"filename": "a"}, content=b"x" * 11)

    assert unknown.status_code == 400 and unknown.json()["error"]["field"] == "doc_type"
    assert empty.status_code == 400
    assert huge.status_code == 413


async def test_the_registry_lists_core_and_pack_types(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    types = {t["key"]: t for t in (await client.get("/document-types")).json()}

    assert {"invoice", "bank_receipt", "cmr", "pod_photo", "e_fatura", "e_irsaliye"} <= set(types)
    assert types["cmr"]["proof_of_delivery"] is True and types["cmr"]["pack"] == "logistics"
    assert "ettn" in types["e_fatura"]["fields"]["properties"]


async def test_a_stranger_cannot_list_documents(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/documents")).status_code == 401
