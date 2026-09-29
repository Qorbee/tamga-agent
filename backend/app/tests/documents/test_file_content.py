import io

import httpx
import openpyxl
import pytest

from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n rest of the image"


async def upload(client: httpx.AsyncClient, filename: str, data: bytes, content_type: str) -> str:
    response = await client.post(
        "/documents",
        params={"filename": filename},
        content=data,
        headers={"content-type": content_type},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["document"]["id"])


async def test_a_verified_image_is_shown_and_anything_else_only_downloaded(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    # A mail client's generic type: the name says what it is, and the bytes agree.
    photo = await upload(client, "teslim.png", PNG, "application/octet-stream")
    # A web page claiming to be a PDF.
    fake = await upload(client, "fatura.pdf", b"<html><script>x()</script>", "application/pdf")

    detail = (await client.get(f"/documents/{photo}")).json()
    shown = await client.get(f"/documents/{photo}/content")
    refused = await client.get(f"/documents/{fake}/content")

    assert detail["preview"] == "image"
    assert shown.content == PNG and shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline;")
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in shown.headers["content-security-policy"]
    assert shown.headers["cache-control"] == "no-store"
    assert refused.headers["content-type"] == "application/octet-stream"
    assert refused.headers["content-disposition"].startswith("attachment;")


async def test_a_spreadsheet_is_read_into_rows(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Plaka", "Tutar"])
    sheet.append(["34 ABC 12", 1250])
    out = io.BytesIO()
    book.save(out)
    xlsx = await upload(client, "sefer.xlsx", out.getvalue(), "application/octet-stream")
    photo = await upload(client, "teslim.png", PNG, "image/png")

    workbook = (await client.get(f"/documents/{xlsx}/sheets")).json()
    not_a_sheet = await client.get(f"/documents/{photo}/sheets")

    [read] = workbook["sheets"]
    assert [[cell["value"] for cell in row["cells"]] for row in read["rows"]] == [
        ["Plaka", "Tutar"],
        ["34 ABC 12", 1250],
    ]
    assert workbook["omitted"] == 0
    assert not_a_sheet.status_code == 400


async def test_another_company_cannot_read_the_file(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    photo = await upload(client, "teslim.png", PNG, "image/png")

    await guest_client(client)  # now signed in to a second sandbox

    assert (await client.get(f"/documents/{photo}/content")).status_code == 404
    assert (await client.get(f"/documents/{photo}/sheets")).status_code == 404
