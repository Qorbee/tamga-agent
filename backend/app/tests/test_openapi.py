import re

from app.main import create_app
from app.openapi import OPENAPI_PATH, render


def test_committed_document_is_current() -> None:
    # The frontend generates its types from this file, so a stale copy types the client against
    # an API that no longer exists. Fix: uv run python scripts/dump_openapi.py
    assert OPENAPI_PATH.read_text() == render()


def test_operation_ids_are_unique_and_stable() -> None:
    schema = create_app().openapi()
    ids = [op["operationId"] for path in schema["paths"].values() for op in path.values()]

    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9_-]+\.[a-z0-9_]+", op_id) for op_id in ids), ids
