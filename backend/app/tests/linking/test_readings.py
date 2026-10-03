"""What a project's conversations say of its job together: what most of them say, and each firm
in one role."""

from app.modules.linking import service


def test_each_field_is_what_most_conversations_say_a_tie_going_to_the_last() -> None:
    known = service._known(
        [
            {"origin": "Istanbul, Türkiye", "destination": "Munich", "cargo": "coils"},
            {"origin": "Izmir", "destination": "Hamburg", "cargo": "Steel coils"},
            {"origin": "Istanbul", "cargo": "steel coils"},
        ]
    )

    # Two say Istanbul, one town however it is written; each said what the other did not once.
    assert known["origin"] == "Istanbul"
    assert known["destination"] == "Hamburg"
    assert known["cargo"] == "steel coils"


def test_a_place_is_given_as_most_conversations_write_it() -> None:
    places = [{"origin": "London, UK"}, {"origin": "London, UK"}, {"origin": "London, Canada"}]
    assert service._known(places)["origin"] == "London, UK"


def test_of_two_clients_the_one_most_conversations_name_holds() -> None:
    known = service._known(
        [{"client": "Stone GmbH"}, {"client": "Rock AG"}, {"client": "STONE GMBH"}]
    )
    assert known["client"] == "STONE GMBH"

    assert service._known([{"client": "Stone GmbH"}, {"client": "Rock AG"}])["client"] == "Rock AG"


def test_a_firm_holds_one_role_in_a_project() -> None:
    known = service._known(
        [
            {"client": "Stone GmbH", "subcontractor": "Hauler Transport"},
            {"party": "Hauler Transport", "party_role": "subcontractor"},
            # One conversation misreads the carrier as the client.
            {"client": "Hauler Transport"},
            {"subcontractor": "Swift Trucks"},
        ]
    )

    assert known["client"] == "Stone GmbH"
    assert known["subcontractors"] == ["Hauler Transport", "Swift Trucks"]


def test_a_reading_that_contradicts_itself_does_not_put_one_firm_in_two_roles() -> None:
    known = service._known(
        [
            # Named the subcontractor, and the client as the firm the mail deals with.
            {
                "subcontractor": "Stone GmbH",
                "party": "Stone GmbH",
                "party_role": "client",
                "origin": "Erbil",
                "destination": "Hamburg",
            },
            {"client": "Stone GmbH"},
        ]
    )

    assert known["client"] == "Stone GmbH"
    assert "subcontractors" not in known
    assert service._title(known) == "Stone GmbH · Erbil → Hamburg"


def test_a_reading_counts_once_for_a_firm_in_a_role() -> None:
    # The client field and the firm the mail deals with name the same client: one voice.
    known = service._known(
        [
            {"client": "Stone GmbH", "party": "Stone GmbH", "party_role": "client"},
            {"client": "Rock AG"},
        ]
    )
    assert known["client"] == "Rock AG"
