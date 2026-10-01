import struct

import pytest

from app.modules.documents import richtext
from app.tests.documents import archives

# The example in Microsoft's description of the format ([MS-OXRTFCP], compressing "hello world").
SPEC_COMPRESSED = bytes.fromhex(
    "2d0000002b0000004c5a4675f1c5c7a703000a007263706731323542320af3"
    "2068656c090020627705b06c647d0a800fa0"
)
SPEC_RTF = b"{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n"


def wrapped(body: str, *, header: str = "\\ansicpg1254") -> bytes:
    """RTF as a mail client wraps HTML in it: the mark, a font table, then ``body``."""
    return (
        f"{{\\rtf1\\ansi{header}\\fromhtml1 \\deff0{{\\fonttbl{{\\f0\\fswiss Arial;}}}}{body}}}"
    ).encode("ascii")


def test_compressed_rich_text_comes_back_whole() -> None:
    assert richtext.decompress(SPEC_COMPRESSED) == SPEC_RTF


def test_rich_text_stored_as_it_is_comes_back_as_it_is() -> None:
    assert richtext.decompress(archives.stored_rtf(SPEC_RTF)) == SPEC_RTF


def test_a_stream_cut_short_gives_what_it_held() -> None:
    assert SPEC_RTF.startswith(richtext.decompress(SPEC_COMPRESSED[:-6]))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"LZFu",
        struct.pack("<IIII", 16, 4, 0x12345678, 0) + b"abcd",
        struct.pack("<IIII", 16, richtext.MAX_RTF + 1, 0x75465A4C, 0) + b"abcd",
    ],
)
def test_what_is_not_compressed_rich_text_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError, match="RTF|rich text"):
        richtext.decompress(data)


def test_the_html_a_mail_client_wrapped_comes_back_out() -> None:
    rtf = wrapped(
        "{\\*\\htmltag19 <html>}{\\*\\htmltag50 <body>}{\\*\\htmltag64 <p>}"
        "\\htmlrtf {\\htmlrtf0 Y\\'fckleme tamam\\htmlrtf }\\htmlrtf0"
        '{\\*\\htmltag84 <img src="cid:logo@kaya">}{\\*\\htmltag72 </p>}'
        "{\\*\\htmltag58 </body>}{\\*\\htmltag27 </html>}"
    )

    assert richtext.html_inside(rtf) == (
        '<html><body><p>Yükleme tamam<img src="cid:logo@kaya"></p></body></html>'
    )


def test_what_only_an_rtf_reader_draws_is_left_out() -> None:
    rtf = wrapped(
        "{\\*\\htmltag64 <p>}\\htmlrtf {\\b bold for RTF}\\par\\htmlrtf0 "
        "kept{\\*\\htmltag72 </p>}"
        '{\\*\\mhtmltag84 <a href="rewritten">}{\\*\\unknown skipped}{\\pict\\bin3 }{}}'
    )

    assert richtext.html_inside(rtf) == "<p>kept</p>"


def test_text_is_read_in_the_documents_code_page_and_as_unicode() -> None:
    turkish = wrapped("{\\*\\htmltag64 <p>}\\'de\\'fe\\'d0\\'f0\\'dd\\'fd{\\*\\htmltag72 </p>}")
    assert richtext.html_inside(turkish) == "<p>ŞşĞğİı</p>"

    # \u carries a UTF-16 unit, then stand-ins for a reader without Unicode: one, or as \uc says.
    unicode = wrapped(
        "{\\*\\htmltag64 <p>}\\u351?\\u-10179?\\u-8704?{\\uc2 \\u8364 EUx}{\\uc0 \\u304 }"
        "{\\*\\htmltag72 </p>}",
        header="\\ansicpg1252",
    )
    assert richtext.html_inside(unicode) == "<p>ş\U0001f600€xİ</p>"

    # A code page Python does not know falls back to Western European.
    unknown = wrapped("{\\*\\htmltag64 <p>}\\'e9{\\*\\htmltag72 </p>}", header="\\ansicpg99999")
    assert richtext.html_inside(unknown) == "<p>é</p>"


def test_breaks_tabs_and_escaped_marks_come_through_as_text() -> None:
    rtf = wrapped(
        "{\\*\\htmltag64 <pre>}a\\par b\\tab c \\{d\\} e\\\\f\\~g\\lquote h\\rquote"
        "{\\*\\htmltag72 </pre>}\r\n"
    )

    assert richtext.html_inside(rtf) == "<pre>a\r\nb\tc {d} e\\f g‘h’</pre>"


def test_rich_text_that_wraps_no_html_has_none_inside() -> None:
    assert richtext.html_inside(b"{\\rtf1\\ansi\\ansicpg1254\\deff0 Merhaba\\par}") is None
    assert richtext.html_inside(SPEC_RTF) is None
    assert richtext.html_inside(wrapped("\\htmlrtf nothing but RTF\\htmlrtf0")) is None


def test_broken_rich_text_never_raises() -> None:
    for broken in (
        wrapped("{\\*\\htmltag64 <p>}open {{{ groups"),
        wrapped("{\\*\\htmltag64 <p>}}}}} extra closes"),
        wrapped("{\\*\\htmltag64 <p>}\\'zz\\'f\\u\\bin99999 "),
        wrapped("{\\*\\htmltag64 <p>}trailing backslash\\")[:-1],
    ):
        richtext.html_inside(broken)
