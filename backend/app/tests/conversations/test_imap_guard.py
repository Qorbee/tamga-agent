import pytest

from app.modules.conversations.adapters.imap import WriteRefused, check_command


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("LOGIN", ("owner@example.com", "secret")),
        ("LIST", ('""', "*")),
        ("EXAMINE", ('"INBOX"',)),
        ("UID", ("SEARCH", "UID 5:*")),
        ("UID", ("FETCH", "5", "(BODY.PEEK[] INTERNALDATE)")),
        ("UID", ("FETCH", "5", "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)] RFC822.SIZE)")),
        ("LOGOUT", ()),
    ],
)
def test_reading_commands_pass(name: str, args: tuple[str, ...]) -> None:
    check_command(name, args)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("SELECT", ('"INBOX"',)),  # opens read-write: a later FETCH could mark mail read
        ("STORE", ("1", "+FLAGS", "(\\Seen)")),
        ("COPY", ("1", '"Archive"')),
        ("MOVE", ("1", '"Archive"')),
        ("EXPUNGE", ()),
        ("APPEND", ('"Sent"',)),
        ("DELETE", ('"INBOX"',)),
        ("UID", ("STORE", "5", "+FLAGS", "(\\Seen)")),
        ("UID", ("COPY", "5", '"Archive"')),
        ("UID", ("FETCH", "5", "(BODY[])")),  # BODY[] without PEEK sets \Seen
        ("UID", ("FETCH", "5", "(RFC822)")),
        ("UID", ("FETCH", "5", "(BODY[TEXT] INTERNALDATE)")),
    ],
)
def test_anything_that_could_change_the_mailbox_is_refused(
    name: str, args: tuple[str, ...]
) -> None:
    with pytest.raises(WriteRefused):
        check_command(name, args)
