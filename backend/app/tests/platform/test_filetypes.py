import struct

from app.platform import filetypes
from app.platform.filetypes import Preview

PNG = b"\x89PNG\r\n\x1a\n rest of the image"


def test_a_generic_type_is_recognised_by_its_name() -> None:
    assert filetypes.preview("application/octet-stream", "CMR.PDF") == Preview.PDF
    assert filetypes.preview("application/octet-stream", "yükleme.jpeg") == Preview.IMAGE
    assert filetypes.preview("application/octet-stream", "kantar.jfif") == Preview.IMAGE
    assert filetypes.preview("image/jpg", "photo") == Preview.IMAGE
    assert filetypes.preview("application/vnd.ms-excel", "liste.xls") == Preview.SHEET


def test_what_a_browser_cannot_show_safely_is_shown_another_way_or_downloaded() -> None:
    # A web page is sanitized and framed, a Word file shown as the PDF made of it: never the file.
    assert filetypes.preview("text/html", "page.htm") == Preview.HTML
    assert filetypes.inline_type("text/html", "page.htm", b"<html>") is None
    assert filetypes.preview("application/octet-stream", "letter.doc") == Preview.OFFICE
    assert filetypes.preview("text/xml", "e-fatura") == Preview.TEXT
    # Declared as text, a file whose name says what it is stays that.
    assert filetypes.preview("text/plain", "fatura.pdf") == Preview.PDF
    assert filetypes.preview("application/octet-stream", "setup.exe") is None
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


def test_an_office_file_is_converted_only_as_what_its_bytes_prove() -> None:
    docx = filetypes.DOCX
    assert filetypes.office_extension(docx, b"PK\x03\x04 word/document.xml") == ".docx"
    assert filetypes.office_extension(filetypes.ODT, b"PK\x03\x04 content.xml") == ".odt"
    # Word saves RTF under .doc, and a .docx may be Word 97's: the bytes pick the reader.
    assert filetypes.office_extension(docx, b"{\\rtf1\\ansi text") == ".rtf"
    assert filetypes.office_extension(docx, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == ".doc"
    # LibreOffice would convert any bytes as text: these are no office file.
    assert filetypes.office_extension(filetypes.DOC, b"MZ\x90\x00 a program") is None


def test_a_text_file_is_decoded_by_what_it_says_of_itself() -> None:
    assert filetypes.decode_text("Çekici plakası".encode()) == "Çekici plakası"
    assert filetypes.decode_text(b"\xff\xfe" + "Ş".encode("utf-16-le")) == "Ş"
    prolog = '<?xml version="1.0" encoding="windows-1254"?><a>Şoför</a>'
    assert filetypes.decode_text(prolog.encode("cp1254")) == prolog
    meta = '<meta charset="iso-8859-9"><p>Teslim edildi, ğ</p>'
    assert filetypes.decode_text(meta.encode("iso-8859-9")) == meta
    assert filetypes.decode_text(b"\x89PNG\r\n\x1a\n\x00\x00") is None
    # The charset its declared type names, when the file itself names none.
    declared = filetypes.charset("text/plain; charset=windows-1254")
    assert declared == "windows-1254"
    assert filetypes.decode_text("Şoför".encode("cp1254"), declared) == "Şoför"


def test_a_pictures_size_is_read_from_its_header() -> None:
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 640, 480) + b"\x08\x02"
    gif = b"GIF89a" + struct.pack("<HH", 32, 16)
    bmp = b"BM" + bytes(16) + struct.pack("<ii", 300, -200)
    # A JPEG's size is in its frame header, after the tables a camera writes before it.
    table = b"\xff\xe0" + struct.pack(">H", 16) + bytes(14)
    frame = b"\xff\xc2" + struct.pack(">HBHH", 17, 8, 1080, 1920)
    jpeg = b"\xff\xd8" + table + frame + bytes(12)
    webp = b"RIFF" + bytes(4) + b"WEBPVP8X" + bytes(8) + (799).to_bytes(3, "little")
    webp += (599).to_bytes(3, "little")

    assert filetypes.picture_size(png) == (640, 480)
    assert filetypes.picture_size(gif) == (32, 16)
    assert filetypes.picture_size(bmp) == (300, 200)
    assert filetypes.picture_size(jpeg) == (1920, 1080)
    assert filetypes.picture_size(webp) == (800, 600)
    # A header cut short, a JPEG with no frame, and what is no picture give no size.
    assert filetypes.picture_size(png[:20]) is None
    assert filetypes.picture_size(b"\xff\xd8" + table + b"\xff\xda" + bytes(12)) is None
    assert filetypes.picture_size(b"%PDF-1.7") is None
