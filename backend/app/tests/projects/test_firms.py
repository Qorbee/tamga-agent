"""Whether a firm is a project's in a role: by the party the owner named, or by a name that is
the other's first words, a word after the first perhaps abbreviated."""

import uuid

from app.modules.parties import service as parties
from app.modules.projects.service import Firms


def named(*names: str) -> Firms:
    return Firms(names=frozenset(parties.name_key(name) for name in names))


def test_a_name_agrees_by_its_first_words_and_its_abbreviations() -> None:
    acme = named("Acme Freight")

    assert acme.agree(None, "ACME FREIGHT SERVICES LTD") is True
    assert acme.agree(None, "ACME FRT. SERVICES") is False  # "FRT" is no beginning of "FREIGHT"
    assert named("Acme Transport").agree(None, "ACME TRANS. SERVICES") is True
    # The first word is the firm's: another first word is another firm.
    assert acme.agree(None, "ACMECO FREIGHT") is False
    assert acme.agree(None, None) is False
    # Too short a name says nothing of one firm.
    assert named("Ace").agree(None, "ACE LOGISTICS") is False


def test_the_owners_party_agrees_and_no_firm_known_says_nothing() -> None:
    party_id = uuid.uuid4()

    assert Firms(party_ids=frozenset({party_id})).agree(party_id, "Anything") is True
    assert Firms().agree(party_id, "ACME FREIGHT") is None
