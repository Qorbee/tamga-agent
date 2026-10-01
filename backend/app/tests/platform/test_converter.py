import httpx
import pytest

from app.platform import converter
from app.platform.converter import Unconvertible
from app.platform.isolated import Reason


def answering(status: int, body: bytes = b"") -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        # The file goes under a name of Tamga's, never the sender's.
        assert b'filename="document.docx"' in request.content
        return httpx.Response(status, content=body)

    return httpx.MockTransport(respond)


async def test_a_pdf_comes_back() -> None:
    pdf = await converter.to_pdf(b"PK", ".docx", transport=answering(200, b"%PDF-1.7 pages"))

    assert pdf == b"%PDF-1.7 pages"


@pytest.mark.parametrize(
    ("status", "body", "reason", "retry"),
    [
        (503, b"", Reason.LIMITS, False),  # its timeout
        (413, b"", Reason.TOO_LARGE, False),
        (500, b"", Reason.DAMAGED, True),  # LibreOffice failed: the file, or the moment
        (400, b"", Reason.DAMAGED, False),
        (200, b"<html>not a pdf", Reason.DAMAGED, False),
    ],
)
async def test_what_the_converter_refuses_says_why(
    status: int, body: bytes, reason: Reason, retry: bool
) -> None:
    with pytest.raises(Unconvertible) as refused:
        await converter.to_pdf(b"PK", ".docx", transport=answering(status, body))

    assert (refused.value.reason, refused.value.retry) == (reason, retry)


async def test_a_file_past_the_limit_is_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(converter, "MAX_INPUT", 3)

    with pytest.raises(Unconvertible) as refused:
        await converter.to_pdf(b"PK\x03\x04", ".docx", transport=answering(200, b"%PDF"))

    assert refused.value.reason == Reason.TOO_LARGE
