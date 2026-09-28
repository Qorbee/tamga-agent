from app.platform.db import Base, import_all_models
from app.platform.tenancy import GLOBAL_TABLES


def test_every_business_table_belongs_to_a_company() -> None:
    # ADR-0003: a table without company_id is a table whose queries no company filter protects.
    import_all_models()
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name in GLOBAL_TABLES:
            continue
        column = table.columns.get("company_id")
        targets = (
            {fk.target_fullname for fk in column.foreign_keys} if column is not None else set()
        )
        if column is None or column.nullable or targets != {"companies.id"}:
            missing.append(table.name)

    assert missing == [], f"add CompanyScoped to {missing}, or name them in GLOBAL_TABLES"
