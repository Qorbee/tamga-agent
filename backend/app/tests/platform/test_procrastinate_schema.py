from importlib.resources import files

# The newest procrastinate schema migration our revisions include: the vendored schema of
# procrastinate 3.9.0. Raise it only in the commit whose revision applies the newer files.
SCHEMA_THROUGH = "03.04.00_50_post_add_retry_failed_job_procedure.sql"


def test_the_installed_procrastinate_needs_no_schema_we_lack() -> None:
    shipped = sorted(
        entry.name
        for entry in (files("procrastinate") / "sql" / "migrations").iterdir()
        if entry.name.endswith(".sql")
    )

    newer = [name for name in shipped if name > SCHEMA_THROUGH]
    assert newer == [], (
        f"procrastinate ships schema migrations {newer}: add an Alembic revision that runs them in "
        "order (as app/migrations/versions/*procrastinate_schema.py runs the base schema), then "
        "raise SCHEMA_THROUGH"
    )
