import uuid

from pydantic import BaseModel, ConfigDict

from app.modules.companies.models import CompanyKind, CompanyMode
from app.platform.language import Language


class CompanySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: CompanyKind
    mode: CompanyMode
    language: Language


class CompanyUpdate(BaseModel):
    language: Language
