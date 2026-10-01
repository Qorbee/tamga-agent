from app.modules.conversations.quoted import own_text


def test_an_outlook_reply_keeps_what_was_typed_above_its_header_block() -> None:
    body = """Plates are 34 ABC 12 and 34 DEF 34.

Regards,
Deniz
________________________________
From: Ayşe Kaya <ayse@kaya.example>
Sent: Monday, September 28, 2026 09:00
To: Deniz Arslan <deniz@acme.example>
Subject: Munich load

Please send the plates for tomorrow's load, IBAN TR33 0006 1005 1978 6457 8413 26."""

    assert (
        own_text(body, "RE: Munich load")
        == "Plates are 34 ABC 12 and 34 DEF 34.\n\nRegards,\nDeniz"
    )


def test_the_header_block_is_read_in_the_partners_languages() -> None:
    turkish = """Yükleme yarın 08:00.

Kimden: Ayşe Kaya
<mailto:ayse@kaya.example>
Gönderme tarihi: 28 Eylül 2026 Pazartesi 09:00
Kime: Deniz Arslan
Konu: Münih yüklemesi

Plakaları gönderir misiniz?"""
    french = """Bien reçu.

De : Jean Martin <jean@transport.example>
Envoyé : lundi 28 septembre 2026 09:00
À : Deniz Arslan
Objet : Chargement

Merci d'envoyer le CMR."""

    assert own_text(turkish, "YNT: Münih yüklemesi") == "Yükleme yarın 08:00."
    assert own_text(french, "RE: Chargement") == "Bien reçu."


def test_a_gmail_reply_ends_at_its_wrote_line_even_when_it_wraps() -> None:
    body = """Confirmed, the truck loads at 10.

On Mon, 28 Sep 2026 at 09:00, Ayşe Kaya <ayse@kaya.example>
wrote:
> Can you load tomorrow?
> Order no 4500123"""

    assert own_text(body, "Re: Load") == "Confirmed, the truck loads at 10."


def test_an_original_message_rule_and_quoted_lines_are_cut() -> None:
    body = """Invoice attached.
> an inline quote left in the reply

-----Original Message-----
From: ops@kaya.example
Please invoice the September loads."""

    assert own_text(body, "RE: Invoice") == "Invoice attached."


def test_an_orders_route_is_not_taken_for_a_header_block() -> None:
    body = """New load:
From: Istanbul
To: Munich
Date: 05.10.2026
Cargo: 24 pallets"""

    assert own_text(body, "New order") == body


def test_a_forward_keeps_everything_it_carries() -> None:
    body = """See the order below.

From: Ayşe Kaya <ayse@kaya.example>
Sent: Monday, September 28, 2026 09:00
Subject: Order 4500123

Two trucks to Munich."""

    assert own_text(body, "FW: Order 4500123") == body
    assert own_text(body, "İLT: Order 4500123") == body


def test_a_mail_that_only_quotes_has_no_own_text() -> None:
    body = """From: Ayşe Kaya <ayse@kaya.example>
Sent: Monday, September 28, 2026 09:00
Subject: Load

Tomorrow?"""

    assert own_text(body, "RE: Load") == ""


def test_an_order_with_a_timed_date_line_keeps_its_route() -> None:
    body = """New order for tomorrow.
From: Istanbul
To: Munich
Date: 05.10.2026 09:00
Order no 4500123, 24 pallets."""

    assert own_text(body, "Order") == body


def test_a_date_line_below_a_from_address_starts_the_history() -> None:
    body = """Confirmed.

From: Ayşe Kaya <ayse@kaya.example>
Date: 28.09.2026 09:00
To: Deniz Arslan

Order no 4500123."""

    assert own_text(body, "RE: Order") == "Confirmed."
