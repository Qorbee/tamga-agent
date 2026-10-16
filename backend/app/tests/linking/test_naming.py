from app.modules.linking.naming import checked

SHOWN = """client: Ocean Pearl Ltd
origin: Gebze
destination: Zakho
cargo: shower gel
first day: 05.10.2026
subject: FW: Load for Zakho
subject: Invoice 2026-118
a mail begins: Please see the order attached"""


def test_a_name_of_words_it_was_given_is_kept() -> None:
    name = "Ocean Pearl · Gebze → Zakho · Shower Gel · 05.10.2026"
    assert checked(f'"{name}."', SHOWN) == name


def test_a_word_it_was_not_given_is_refused() -> None:
    assert checked("Ocean Pearl · Gebze → Zakho · fifth truck", SHOWN) is None


def test_a_mails_own_words_name_no_job() -> None:
    assert checked("FW · Zakho", SHOWN) is None
    assert checked("Invoice · 2026", SHOWN) is None


def test_too_little_too_much_none_or_an_address_is_refused() -> None:
    assert checked("Zakho", SHOWN) is None
    assert checked("NONE", SHOWN) is None
    assert checked(" ".join(["Ocean Pearl Gebze Zakho shower gel load for please"]), SHOWN) is None
    assert checked("oceanpearl.com · Zakho", SHOWN + "\noceanpearl.com") is None
