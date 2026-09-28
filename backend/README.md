# Backend

The API service: FastAPI on PostgreSQL, async throughout, one Docker image for every environment.
The worker that runs background jobs will be the same image with another command.

## Map

| Path | What is there |
|---|---|
| `app/main.py` | The app factory: lifespan, CORS, error mapping, routers |
| `app/config.py` | `Settings` — the only place the environment is read |
| `app/platform/` | Infrastructure every module uses: the database session, domain errors |
| `app/routers/health.py` | `/health` (liveness, touches nothing) and `/health/ready` (checks Postgres) |
| `app/modules/` | Business domains, one package each ([shape](app/modules/README.md)) |
| `app/migrations/` | Alembic; models are discovered from `app/modules/*/models.py` |
| `openapi.json` | The API contract the frontend generates its types from. Generated, committed |

## Start reading at

`app/main.py`, then `app/modules/README.md` for where new code goes. How the modules fit together:
`docs/architecture/02-backend-modules.md`.

## Running it

From `backend/`, with `docker compose up -d` running at the repository root:

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload     # http://localhost:8000/docs
```

## Checks

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
uv run pytest -q                          # add INTEGRATION=1 to include tests that need Postgres
uv run python scripts/dump_openapi.py     # after an API change
```

The integration tests reach Postgres on 5432 and storage on 8333 (`app/tests/conftest.py`). If the
root `.env` moved those ports, export `DATABASE_URL` and `S3_ENDPOINT_URL` with the new ports first.

## Rules that apply here

`AGENTS.md` in this directory and the root `AGENTS.md`.
