import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_needs_no_backing_services() -> None:
    # No `with`: the lifespan does not run, so nothing connects to Postgres or Redis.
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_readiness_reaches_postgres_and_redis() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "redis": True}
