import uuid

from pydantic import BaseModel, ConfigDict

from app.modules.companies.models import CompanyKind, CompanyMode


class CompanySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: CompanyKind
    mode: CompanyMode
