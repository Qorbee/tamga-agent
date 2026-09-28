"""procrastinate's job-queue schema, as shipped with procrastinate 3.9.0.

Revision ID: 7b1a40a4e419
Revises: 726c55e0e949
Create Date: 2026-09-28 19:44:02.175303

The SQL is a copy of the package's ``sql/schema.sql``, kept beside the migrations so this revision
builds the same schema whatever procrastinate version is installed later. An upgrade that changes
the schema ships its own ``sql/migrations/*.sql`` files, applied by a new revision.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import context, op
from sqlalchemy.util import await_only

revision: str = "7b1a40a4e419"
down_revision: str | Sequence[str] | None = "726c55e0e949"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "procrastinate-3.9.0-schema.sql"

# Tables first (CASCADE takes their triggers), then every procrastinate function, then the types.
DROP = """
DROP TABLE IF EXISTS procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs,
    procrastinate_workers CASCADE;
DO $$
DECLARE fn regprocedure;
BEGIN
    FOR fn IN SELECT oid::regprocedure FROM pg_proc WHERE proname LIKE 'procrastinate\\_%' LOOP
        EXECUTE 'DROP FUNCTION ' || fn || ' CASCADE';
    END LOOP;
END $$;
DROP TYPE IF EXISTS procrastinate_job_to_defer_v1, procrastinate_job_event_type,
    procrastinate_job_status;
"""


def _execute_script(sql: str) -> None:
    if context.is_offline_mode():
        # `alembic upgrade --sql` renders the script as it is.
        op.execute(sql)
        return
    # asyncpg runs SQLAlchemy statements as prepared statements, which hold one command each. The
    # driver's own execute() takes a whole script, inside the transaction Alembic already opened.
    driver = op.get_bind().connection.driver_connection
    if driver is None:
        raise RuntimeError("the migration has no database connection")
    await_only(driver.execute(sql))


def upgrade() -> None:
    _execute_script(SCHEMA.read_text())


def downgrade() -> None:
    _execute_script(DROP)
