"""The OpenAPI document as committed to ``backend/openapi.json`` — the frontend's type source.

Built from ``create_app().openapi()``: no server, database or Redis needed. Keys are sorted so the
file changes when the API changes, not when two ``include_router`` calls swap places.
"""

import json
from pathlib import Path

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    from app.main import create_app

    schema = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
