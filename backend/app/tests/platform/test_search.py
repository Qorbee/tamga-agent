from app.platform import search


def test_text_folds_alike_however_it_was_written() -> None:
    assert (
        search.fold("İSTANBUL") == search.fold("istanbul") == search.fold("ıstanbul") == "istanbul"
    )
    assert search.fold("Ödeme Çağrısı") == "odeme cagrisi"
    assert search.fold("CMR-34ABC12.pdf") == "cmr 34abc12 pdf"
    assert search.fold("ＡＢＣ１２ Straße") == "abc12 strasse"
    assert search.fold("--- %_ ---") == ""


def test_fields_are_kept_apart_after_a_leading_space() -> None:
    assert search.search_text("Re: Münih", None, "", "ops@kaya.example") == (
        " re munih | ops kaya example"
    )


def test_a_query_splits_into_words_phrases_and_numbers() -> None:
    assert search.terms('Fatura "Ayşe Kaya" 34 ABC-12 %') == [
        search.Term("fatura", anywhere=False),
        search.Term("ayse kaya", anywhere=False),
        search.Term("34", anywhere=True),
        search.Term("abc 12", anywhere=True),
    ]
    # An unclosed quote still reads as a phrase.
    assert search.terms('"teslim tarihi') == [search.Term("teslim tarihi", anywhere=False)]
    assert search.Term("abc 12", anywhere=True).compact == "abc12"
