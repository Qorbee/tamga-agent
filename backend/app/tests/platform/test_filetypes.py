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
    assert filetypes.preview("application/msword", "letter.doc") is None
    # An archive opens as the files taken out of it, and is itself only downloaded.
    assert filetypes.preview("application/zip", "files.zip") == Preview.ARCHIVE
    assert filetypes.inline_type("application/zip", "files.zip", b"PK\x03\x04") is None


def test_an_archive_is_known_by_its_name_and_proven_by_its_bytes() -> None:
    # Mail clients label an .xlsx (a zip inside) as a zip: it is still a spreadsheet.
    assert filetypes.preview("application/zip", "liste.xlsx") == Preview.SHEET
    assert filetypes.archive_type("application/octet-stream", "WINMAIL.DAT") == filetypes.TNEF
    assert filetypes.archive_type("application/zip", "Belgeler") == filetypes.ZIP
    assert filetypes.archive_type("", "yedek.tar.gz") == filetypes.TAR
    assert filetypes.archive_kind("", "a.rar", b"Rar!\x1a\x07\x01\x00") == "rar"
    # An extension that says nothing leaves it to the declared type; one that says another kind
    # does not.
    assert filetypes.archive_type("application/zip", "ATT00002.bin") == filetypes.ZIP
    assert filetypes.archive_type("application/zip", "rapor.docx") is None
    assert filetypes.extension("Yedek.TAR.GZ") == ".TAR.GZ"
    assert filetypes.extension("fatura.pdf") == ".pdf" and filetypes.extension("README") == ""
    # A .zip whose bytes are a PDF is not opened as one.
    assert filetypes.archive_kind("application/zip", "a.zip", b"%PDF-1.7") is None


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
