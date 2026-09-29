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
    for gone in ("script", "steal", "iframe", "form", "input", "Seç", "Sayfa"):
        assert gone not in out
    # CSS is kept whole: in the frame it can only style the mail.
    assert "<style>p{margin:0}</style>" in out and 'style="color:red;position:fixed"' in out
    assert out.count("href=") == 1
    assert (
        '<a href="https://kaya.example/takip" target="_blank" rel="noopener noreferrer nofollow">'
        in out
    )


def test_images_the_mail_carries_show_and_web_images_keep_their_address() -> None:
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
    assert shown.html.count("src=") == 4
    assert '<img src="cid:logo@kaya" alt="Kaya">' in shown.html
    assert '<img src="data:image/png;base64,iVBORw0KGgo=">' in shown.html
    # The web app holds these until the reader asks for them.
    assert '<img src="https://track.example/open.gif" width="1" height="1">' in shown.html
    assert '<img src="http://kaya.example/banner.png">' in shown.html
    assert "fake" not in shown.html and "svg" not in shown.html


def test_the_body_keeps_its_background_and_style_as_its_client_laid_it_out() -> None:
    shown = html.render(
        mail_with(
            '<html><body bgcolor="#f4f4f4" style="margin:0;background:#eee" onload="steal()"'
            ' link="#00f"><div style="box-shadow:0 1px 2px #000;'
            "background:url('https://kaya.example/bg.png')\">Merhaba</div></body></html>"
        )
    )

    assert shown is not None
    assert shown.html.startswith('<body bgcolor="#f4f4f4" style="margin:0;background:#eee">')
    assert "steal" not in shown.html and "link=" not in shown.html
    assert "box-shadow:0 1px 2px #000" in shown.html
    # A picture drawn by the CSS waits for Show pictures, as an image on the web does.
    assert shown.remote_images == 1


def test_a_body_with_nothing_to_keep_adds_no_body_tag() -> None:
    shown = html.render(mail_with("<html><body onload='steal()'><p>Merhaba</p></body></html>"))

    assert shown is not None and shown.html.strip() == "<p>Merhaba</p>"


def test_a_mail_with_no_html_has_none() -> None:
    message = EmailMessage()
    message.set_content("Sadece metin")

    assert html.render(message.as_bytes()) is None
