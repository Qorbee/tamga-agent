from datetime import UTC, datetime
from email.message import EmailMessage

from app.modules.conversations import service
from app.modules.conversations.mail import parse_mail, signature_images
from app.modules.conversations.models import Message

PNG = b"\x89PNG\r\n\x1a\n"


def mail(**headers: str) -> EmailMessage:
    message = EmailMessage()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return message


def test_headers_and_a_turkish_body_are_read() -> None:
    message = mail(
        From="Dispeçer <Dispatch@Carrier.example>",
        To="ops@broker.example",
        Cc="Owner <owner@broker.example>",
        Subject="34 ABC 12 Kapıkule'de",
        Date="Mon, 28 Sep 2026 09:15:00 +0300",
        Message_ID="<a1@carrier.example>",
        In_Reply_To="<a0@broker.example>",
        References="<z@broker.example> <a0@broker.example>",
    )
    message.set_content("Tır Kapıkule'de bekliyor, yarın öğlen teslim.", charset="windows-1254")

    parsed = parse_mail(message.as_bytes())

    assert parsed.sender == "dispatch@carrier.example" and parsed.sender_name == "Dispeçer"
    assert parsed.recipients == ["ops@broker.example", "owner@broker.example"]
    assert parsed.subject == "34 ABC 12 Kapıkule'de"
    assert parsed.sent_at == datetime(2026, 9, 28, 6, 15, tzinfo=UTC)
    assert parsed.message_id == "<a1@carrier.example>"
    assert parsed.references == ["<z@broker.example>", "<a0@broker.example>"]
    assert parsed.body_text == "Tır Kapıkule'de bekliyor, yarın öğlen teslim."


def test_an_html_only_body_becomes_text() -> None:
    message = mail(From="client@shipper.example", Subject="Order 4521")
    message.set_content(
        "<html><style>p{}</style><body><p>Order <b>4521</b></p><p>10 trucks</p></body></html>",
        subtype="html",
    )

    assert parse_mail(message.as_bytes()).body_text == "Order 4521\n10 trucks"


def test_files_are_found_even_inside_a_forwarded_mail() -> None:
    inner = mail(From="carrier@example.com", Subject="CMR")
    inner.set_content("CMR attached")
    inner.add_attachment(b"%PDF-1.7 cmr", maintype="application", subtype="pdf", filename="CMR.pdf")
    outer = mail(From="ops@broker.example", Subject="Fwd: CMR")
    outer.set_content("See below")
    outer.add_attachment(
        b"\x89PNG screenshot", maintype="image", subtype="png", filename="shot.png"
    )
    outer.add_attachment(inner)

    parsed = parse_mail(outer.as_bytes())

    assert {(a.filename, a.content_type) for a in parsed.attachments} == {
        ("shot.png", "image/png"),
        ("CMR.pdf", "application/pdf"),
    }
    assert parsed.body_text == "See below"


def test_a_text_file_keeps_the_charset_it_was_sent_in() -> None:
    message = mail(From="ops@kaya.example", Subject="Liste")
    message.set_content("Ekte.")
    message.add_attachment(
        "Şoför".encode("cp1254"), maintype="text", subtype="plain", filename="liste.txt"
    )
    [file] = message.iter_attachments()
    file.set_param("charset", "windows-1254")

    [attachment] = parse_mail(message.as_bytes()).attachments

    # A legacy file names its encoding only there, and is decoded by it (filetypes.decode_text).
    assert attachment.content_type == "text/plain; charset=windows-1254"


def test_a_file_the_html_body_shows_is_inline_and_an_attached_one_is_not() -> None:
    message = mail(From="ops@kaya.example", Subject="Teslim")
    message.set_content("Teslim edildi.")
    message.add_alternative(
        '<p>Teslim edildi.</p><img src="cid:logo%40kaya"><a href="cid:fatura@kaya">Fatura</a>'
        '<img src="cid:fake@kaya">',
        subtype="html",
    )
    html = message.get_body(preferencelist=("html",))
    assert html is not None
    html.add_related(PNG + b" logo", "image", "png", cid="<Logo@Kaya>", filename="image001.png")
    # Named by a link, not drawn: a file beside the text.
    html.add_related(b"%PDF fatura", "application", "pdf", cid="<fatura@kaya>", filename="f.pdf")
    # Drawn, but its bytes are no picture: the frame cannot show it, so it stays a file.
    html.add_related(b"<html>", "image", "png", cid="<fake@kaya>", filename="fake.png")
    message.add_attachment(b"\x89PNG photo", maintype="image", subtype="png", filename="teslim.png")
    # A mail forwarded as a file is not drawn, so its files stay beside the text, even one that
    # happens to share a Content-ID with the outer body.
    forwarded = mail(From="carrier@example.com", Subject="CMR")
    forwarded.set_content("CMR")
    forwarded.add_attachment(
        b"\x89PNG cmr", maintype="image", subtype="png", filename="cmr.png", cid="<logo@kaya>"
    )
    message.add_attachment(forwarded)

    parsed = parse_mail(message.as_bytes())

    assert {(a.filename, a.inline) for a in parsed.attachments} == {
        ("image001.png", True),
        ("f.pdf", False),
        ("fake.png", False),
        ("teslim.png", False),
        ("cmr.png", False),
    }


def test_a_picture_below_its_writers_contact_lines_is_their_signature() -> None:
    html = (
        "<p>Truck 34 ABC 12 is loaded, CMR below:</p><img src='cid:cmr'>"
        "<p>Best regards,</p><p>Ada Stone</p><p>Stone GmbH | Operations</p>"
        "<p>T: +49 89 123 456 78</p><p>www.stone.example</p><img src='cid:banner'>"
        "<p>On Mon, Ops wrote:</p><p>&gt; please send the CMR</p>"
    )
    # The banner sits below the phone and the web address; the CMR above the sign-off.
    assert signature_images(html) == {"banner"}
    # A sentence of the writer's own between the contact line and a picture: the picture is
    # theirs to show, not their signature's.
    pasted = (
        "<p>ada@stone.example</p><p>Here is the photo of the seal you asked for this morning:</p>"
        "<img src='cid:seal'>"
    )
    assert signature_images(pasted) == set()
    # One number above a photo is a driver's, not a signature.
    assert signature_images("<p>Driver: +90 555 123 45 67</p><img src='cid:seal'>") == set()
    # A date, an order number or a plate is no contact line.
    assert (
        signature_images("<p>Loading 05.10.2026 09:00, order 4500123456</p><img src='cid:x'>")
        == set()
    )


def test_a_parsed_mail_marks_its_signature_picture() -> None:
    message = mail(From="ada@stone.example", Subject="Loaded")
    message.set_content("Loaded.")
    message.add_alternative(
        "<p>Loaded.</p><p>Ada Stone</p><p>Tel: 0049 89 123</p><p>ada@stone.example</p>"
        "<img src='cid:banner@stone'>",
        subtype="html",
    )
    html = message.get_body(preferencelist=("html",))
    assert html is not None
    html.add_related(PNG + b" port", "image", "png", cid="<banner@stone>", filename="image002.jpg")

    [banner] = parse_mail(message.as_bytes()).attachments
    assert (banner.inline, banner.signature) == (True, True)


def test_a_broken_date_is_left_for_the_server_time() -> None:
    message = mail(From="x@example.com", Date="sometime last week")
    message.set_content("hi")

    assert parse_mail(message.as_bytes()).sent_at is None


def test_what_the_database_would_refuse_is_left_out() -> None:
    # A NUL byte or an impossible address would fail the insert, and the folder would stall on it.
    message = mail(From=f"{'x' * 300}@example.com", To="ops@broker.example", Subject="a\x00b")
    message.set_content("Tır\x00 yolda")

    parsed = parse_mail(message.as_bytes())

    assert parsed.sender is None
    assert parsed.subject == "ab"
    assert parsed.body_text == "Tır yolda"


def test_on_a_shared_provider_only_the_mailbox_itself_is_the_companys() -> None:
    # The company reads me@gmail.com: a customer on gmail.com is not the company.
    copied = Message(recipients=["customer@gmail.com", "me@gmail.com"], copied=["me@gmail.com"])
    assert service._addressed(copied, {"me@gmail.com"}, set()) == "cc"
    written = Message(recipients=["Ops@Acme.example"], copied=[])
    assert service._addressed(written, {"me@acme.example"}, {"acme.example"}) == "to"
    assert service._addressed(written, {"me@gmail.com"}, set()) == "none"
