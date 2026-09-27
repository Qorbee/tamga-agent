# `app/modules/`

One package per business domain. A module owns its tables and its rules; nothing outside it reads
or writes them directly.

```text
modules/<name>/
├── models.py       # SQLAlchemy models (subclass app.platform.db.Base) — Alembic finds this file itself
├── schemas.py      # Pydantic request/response models: the API contract
├── repository.py   # reads and writes this module's tables; returns models, never HTTP responses
├── service.py      # business rules and the transaction; raises app.platform.errors, not HTTPException
└── router.py       # parses input, calls one service operation, shapes the output — nothing else
```

Register the router in `app/main.py` (one line). Tests go in `app/tests/<name>/`.

A module may import `app.platform`; modules do not import each other. When one needs another's
data, the owning module exposes a service function for it — that is the only door.

Rules: `backend/AGENTS.md`.
