from datetime import UTC, datetime
from email.message import EmailMessage

from app.modules.conversations.mail import parse_mail


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

    assert parsed.sender == "dispatch@carrier.example"
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
