from fastapi.testclient import TestClient

from app.main import create_app


def test_a_state_change_from_a_foreign_page_is_refused() -> None:
    # No `with`: the lifespan does not run, and the check answers before any route touches Postgres.
    client = TestClient(create_app())

    response = client.post(
        "/sessions",
        json={"email": "owner@example.com", "password": "anything"},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_a_read_from_a_foreign_page_is_not_the_checks_business() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 200
