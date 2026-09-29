from app.platform import filetypes
from app.platform.filetypes import Preview

PNG = b"\x89PNG\r\n\x1a\n rest of the image"


def test_a_generic_type_is_recognised_by_its_name() -> None:
    assert filetypes.preview("application/octet-stream", "CMR.PDF") == Preview.PDF
    assert filetypes.preview("application/octet-stream", "yükleme.jpeg") == Preview.IMAGE
    assert filetypes.preview("image/jpg", "photo") == Preview.IMAGE
    assert filetypes.preview("application/vnd.ms-excel", "liste.xls") == Preview.SHEET


def test_what_a_browser_cannot_show_safely_is_download_only() -> None:
    assert filetypes.preview("text/html", "page.htm") is None
    assert filetypes.preview("application/zip", "files.zip") is None
    assert filetypes.preview("application/msword", "letter.doc") is None


def test_a_file_is_shown_only_as_what_its_bytes_prove() -> None:
    # A phone's PNG saved as .jpg is still an image: shown, under its real type.
    assert filetypes.inline_type("image/jpeg", "foto.jpg", PNG) == filetypes.PNG
    assert (
        filetypes.inline_type("application/pdf", "cmr.pdf", b"\r\n%PDF-1.4 late") == filetypes.PDF
    )
    # A web page named like a PDF, or an image named like a PDF, is only downloaded.
    assert filetypes.inline_type("application/pdf", "fatura.pdf", b"<html><script>") is None
    assert filetypes.inline_type("application/pdf", "fatura.pdf", PNG) is None
    assert filetypes.inline_type("text/html", "page.html", PNG) is None
