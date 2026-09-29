import hashlib
from email.message import EmailMessage

from app.modules.conversations import html

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def mail_with(body: str, *images: tuple[str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"] = "ops@kaya.example"
    message.set_content("text")
    message.add_alternative(body, subtype="html")
    part = message.get_body(preferencelist=("html",))
    assert part is not None
    for cid, data in images:
        part.add_related(data, "image", "png", cid=f"<{cid}>")
    return message.as_bytes()


def test_nothing_in_the_mail_can_run_or_leave_the_frame() -> None:
    shown = html.render(
        mail_with(
            "<html><head><title>Sayfa</title><style>p{margin:0}</style></head><body>"
            '<p onclick="steal()" style="color:red;position:fixed">Merhaba</p>'
            "<script>steal()</script><iframe src='https://x.example'></iframe>"
            "<form action='https://x.example'><input name='p'><select><option>Seç</option>"
            "</select></form>"
            '<a href="javascript:steal()">a</a><a href="data:text/html,x">b</a>'
            '<a href="#top">c</a><a href="https://kaya.example/takip">d</a></body></html>'
        )
    )

    assert shown is not None
    out = shown.html
    for gone in ("script", "steal", "iframe", "form", "input", "Seç", "Sayfa", "fixed"):
        assert gone not in out
    assert "<style>p{margin:0}</style>" in out and 'style="color:red"' in out
    assert out.count("href=") == 1
    assert (
        '<a href="https://kaya.example/takip" target="_blank" rel="noopener noreferrer nofollow">'
        in out
    )


def test_images_the_mail_carries_show_and_web_images_are_counted_not_fetched() -> None:
    shown = html.render(
        mail_with(
            '<img src="cid:Logo%40Kaya" alt="Kaya">'
            '<img src="https://track.example/open.gif" width="1" height="1">'
            '<img src="http://kaya.example/banner.png">'
            '<img src="cid:fake@kaya"><img src="cid:missing@kaya">'
            '<img src="data:image/svg+xml;base64,PHN2Zz4=">'
            '<img src="data:image/png;base64,iVBORw0KGgo=">',
            ("Logo@Kaya", PNG),
            # Declared a PNG, but its bytes are a page.
            ("fake@kaya", b"<html><script>steal()</script></html>"),
        )
    )

    assert shown is not None and shown.remote_images == 2
    assert shown.images == {"logo@kaya": hashlib.sha256(PNG).digest()}
    assert shown.html.count("src=") == 2
    assert '<img src="cid:logo@kaya" alt="Kaya">' in shown.html
    assert '<img src="data:image/png;base64,iVBORw0KGgo=">' in shown.html
    assert "track.example" not in shown.html and "svg" not in shown.html


def test_a_mail_with_no_html_has_none() -> None:
    message = EmailMessage()
    message.set_content("Sadece metin")

    assert html.render(message.as_bytes()) is None
