"""Office files to PDF, by LibreOffice in a service of its own (ADR-0012): Gotenberg.

A file from outside is opened there, not here: the converter sits on a network with no route out,
with every feature that fetches turned off (docker-compose.yml), so what the file holds reaches
nothing. What comes back is a PDF like any other, read in the locked child (isolated.py).
"""

from typing import Final

import httpx

from app.config import get_settings
from app.platform.isolated import Reason

# Past these the file, or what it became, is too much to convert or read: the converter's own
# limit (docker-compose.yml) and the most a rendition may be.
MAX_INPUT: Final = 50 * 1024 * 1024
MAX_OUTPUT: Final = 100 * 1024 * 1024
# Beyond the converter's own 60 s: a queue of files may wait for LibreOffice.
_TIMEOUT: Final = httpx.Timeout(120.0, connect=10.0)
_PATH: Final = "/forms/libreoffice/convert"


class Unconvertible(Exception):
    """The converter could not make a PDF of the file. ``retry`` when it may be the moment, not the
    file: LibreOffice failing, which it also does short of memory."""

    def __init__(self, reason: Reason, *, retry: bool = False) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.retry = retry


def configured() -> bool:
    return get_settings().converter_url is not None


async def to_pdf(
    data: bytes, extension: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """The file as a PDF. ``extension`` (``.docx``) picks LibreOffice's reader: it is the kind the
    file's first bytes proved, and the name sent is only ``document`` with it, never the sender's.
    Raises ``Unconvertible``; a converter that cannot be reached raises httpx's error."""
    url = get_settings().converter_url
    if url is None:
        raise Unconvertible(Reason.UNSUPPORTED)
    if len(data) > MAX_INPUT:
        raise Unconvertible(Reason.TOO_LARGE)
    files = {"files": (f"document{extension}", data)}
    async with (
        httpx.AsyncClient(base_url=url, timeout=_TIMEOUT, transport=transport) as http,
        http.stream("POST", _PATH, files=files) as response,
    ):
        if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            raise Unconvertible(Reason.LIMITS)  # its timeout
        if response.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE:
            raise Unconvertible(Reason.TOO_LARGE)
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise Unconvertible(Reason.DAMAGED, retry=True)
        if response.status_code != httpx.codes.OK:
            raise Unconvertible(Reason.DAMAGED)
        pdf = bytearray()
        async for chunk in response.aiter_bytes():
            pdf += chunk
            if len(pdf) > MAX_OUTPUT:
                raise Unconvertible(Reason.TOO_LARGE)
    if not pdf.startswith(b"%PDF-"):
        raise Unconvertible(Reason.DAMAGED)
    return bytes(pdf)
