import base64
import hashlib

import pytest

from app.modules.documents import unpacking
from app.modules.documents.unpacking import Kind, Request, Unpacked
from app.platform import isolated
from app.platform.isolated import Reason, Refused
from app.tests.documents import archives

FILES = {"cmr/CMR 1.pdf": b"%PDF-1.7 one", "photo.jpg": b"\xff\xd8\xff photo"}


def unpack(data: bytes, kind: Kind, **request: object) -> Unpacked:
    hint = Request(kind=kind, **request).model_dump_json()  # type: ignore[arg-type]
    return Unpacked.model_validate(unpacking.unpack(data, hint))


def taken(unpacked: Unpacked) -> dict[str, bytes]:
    return {m.name: base64.b64decode(m.data) for m in unpacked.members if m.data is not None}


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (Kind.ZIP, archives.zip_of(FILES)),
        (Kind.RAR, archives.rar5_of(FILES)),
        (Kind.SEVEN_ZIP, archives.seven_zip_of(FILES)),
        (Kind.TAR, archives.tar_gz_of(FILES)),
    ],
)
def test_every_archive_kind_gives_its_files_under_their_names(kind: Kind, data: bytes) -> None:
    unpacked = unpack(data, kind)

    assert taken(unpacked) == FILES
    assert unpacked.next is None and unpacked.omitted == 0


def test_a_winmail_dat_gives_the_files_outlook_wrapped() -> None:
    data = archives.winmail_of({"CMR 1.pdf": b"%PDF-1.7 one", "Teslim.jpg": b"\xff\xd8\xff"})

    assert taken(unpack(data, Kind.TNEF)) == {
        "CMR 1.pdf": b"%PDF-1.7 one",
        "Teslim.jpg": b"\xff\xd8\xff",
    }


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WRAPPED_HTML = (
    b"{\\rtf1\\ansi\\ansicpg1254\\fromhtml1 \\deff0{\\fonttbl{\\f0\\fswiss Arial;}}"
    b"{\\*\\htmltag64 <p>}Y\\'fckleme tamam"
    b'{\\*\\htmltag84 <img src="cid:logo@kaya">}{\\*\\htmltag72 </p>}}'
)


def test_a_winmail_dat_gives_the_body_its_sender_laid_out_and_the_pictures_it_draws() -> None:
    data = archives.winmail_of(
        {"logo.png": PNG, "CMR 1.pdf": b"%PDF-1.7 one", "not-a-picture.png": b"MZ program"},
        rtf_body=archives.stored_rtf(WRAPPED_HTML),
        content_ids={"logo.png": "<Logo@Kaya>", "not-a-picture.png": "<trap@kaya>"},
    )

    body = unpacking.MailBody.model_validate(unpacking.tnef_body(data, ""))

    assert body.html == '<p>Yükleme tamam<img src="cid:logo@kaya"></p>'
    # By what its bytes are, never by its name; and a Content-ID as a `cid:` names it.
    assert body.images == {"logo@kaya": hashlib.sha256(PNG).hexdigest()}
    # The files still come out as files.
    assert set(taken(unpack(data, Kind.TNEF))) == {"logo.png", "CMR 1.pdf", "not-a-picture.png"}


def test_a_body_kept_as_html_is_read_in_the_code_page_the_file_names() -> None:
    turkish = archives.winmail_of({}, html_body="<p>Yükleme şimdi</p>".encode("cp1254"))
    assert unpacking.MailBody.model_validate(unpacking.tnef_body(turkish, "")).html == (
        "<p>Yükleme şimdi</p>"
    )

    utf8 = archives.winmail_of({}, html_body="<p>Yükleme şimdi</p>".encode())
    assert unpacking.MailBody.model_validate(unpacking.tnef_body(utf8, "")).html == (
        "<p>Yükleme şimdi</p>"
    )


def test_a_winmail_dat_whose_body_is_rich_text_or_missing_has_no_html() -> None:
    rich = archives.winmail_of({}, rtf_body=archives.stored_rtf(b"{\\rtf1\\ansi Merhaba\\par}"))
    assert unpacking.MailBody.model_validate(unpacking.tnef_body(rich, "")).html is None

    plain = archives.winmail_of({"CMR 1.pdf": b"%PDF-1.7 one"})
    assert unpacking.MailBody.model_validate(unpacking.tnef_body(plain, "")).html is None


def test_a_winmail_dat_whose_body_is_not_rich_text_is_damaged() -> None:
    data = archives.winmail_of({}, rtf_body=b"not compressed rich text at all")

    with pytest.raises(Refused) as refused:
        unpacking.tnef_body(data, "")
    assert refused.value.reason == Reason.DAMAGED

    with pytest.raises(Refused):
        unpacking.tnef_body(b"not a winmail.dat", "")


def test_what_systems_leave_behind_is_skipped_and_a_name_is_only_text() -> None:
    data = archives.zip_of(
        {
            "Thumbs.db": b"thumbs",
            "photos/.DS_Store": b"finder",
            "__MACOSX/photos/._a.jpg": b"fork",
            "../../etc/passwd": b"not a path",
            "empty.txt": b"",
        }
    )

    assert taken(unpack(data, Kind.ZIP)) == {"../../etc/passwd": b"not a path"}


def test_a_name_not_in_utf8_is_read_in_the_dos_code_page() -> None:
    data = archives.zip_named_in("cp437", "Çek.pdf", b"%PDF")

    assert [member.name for member in unpack(data, Kind.ZIP).members] == ["Çek.pdf"]


def test_a_member_locked_by_a_password_says_so() -> None:
    [member] = unpack(archives.encrypted_zip("secret.pdf", b"%PDF-1.7"), Kind.ZIP).members

    assert (member.name, member.data, member.refused) == ("secret.pdf", None, Reason.PASSWORD)


def test_an_archive_with_encrypted_headers_is_locked_not_damaged() -> None:
    with pytest.raises(Refused) as locked:
        unpack(archives.rar5_with_encrypted_headers(), Kind.RAR)

    assert locked.value.reason == Reason.PASSWORD


def test_a_bomb_is_refused_by_its_ratio_and_its_total(monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = archives.zip_of({"zeros.bin": b"\0" * (5 * 1024 * 1024)})
    with pytest.raises(Refused) as ratio:
        unpack(bomb, Kind.ZIP)

    monkeypatch.setattr(unpacking, "MAX_TOTAL", 10)
    with pytest.raises(Refused) as total:
        unpack(archives.zip_of({"a.txt": b"eleven byte"}), Kind.ZIP)

    assert ratio.value.reason == total.value.reason == Reason.TOO_LARGE


def test_files_past_the_cap_are_counted_not_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(unpacking, "MAX_MEMBERS", 2)

    unpacked = unpack(archives.zip_of({"a": b"1", "b": b"2", "c": b"3"}), Kind.ZIP)

    assert list(taken(unpacked)) == ["a", "b"] and unpacked.omitted == 1


def test_a_large_archive_comes_back_in_runs_that_share_its_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(unpacking, "BATCH_BYTES", 1)
    data = archives.zip_of({"a": b"11", "b": b"22", "c": b"33"})

    runs = [unpack(data, Kind.ZIP)]
    while runs[-1].next is not None:
        runs.append(unpack(data, Kind.ZIP, start=runs[-1].next, spent=runs[-1].spent))

    # A run that fills up cannot know it took the last file, so the run after it may find none.
    assert [list(taken(run)) for run in runs if run.members] == [["a"], ["b"], ["c"]]
    assert runs[-1].spent == 6
    # What earlier runs took out counts against the total.
    monkeypatch.setattr(unpacking, "MAX_TOTAL", 5)
    with pytest.raises(Refused):
        unpack(data, Kind.ZIP, start=2, spent=4)


def test_bytes_that_are_not_the_archive_are_damaged() -> None:
    with pytest.raises(Refused) as damaged:
        unpack(b"PK\x03\x04 not really a zip", Kind.ZIP)

    assert damaged.value.reason == Reason.DAMAGED


async def test_an_archive_is_unpacked_in_the_locked_child() -> None:
    hint = Request(kind=Kind.ZIP).model_dump_json()

    outcome = await isolated.run("documents.unpack", archives.zip_of({"Çek ı.pdf": b"%PDF"}), hint)

    assert taken(Unpacked.model_validate(outcome.value)) == {"Çek ı.pdf": b"%PDF"}
