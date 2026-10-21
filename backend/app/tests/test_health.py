import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_needs_no_backing_services() -> None:
    # No `with`: the lifespan does not run, so nothing connects to Postgres.
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_readiness_reaches_postgres_and_storage(storage_bucket: None) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "storage": True}


def test_a_deployment_serves_no_pages_about_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "production")
    client = TestClient(create_app())

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.get("/health").status_code == 200
