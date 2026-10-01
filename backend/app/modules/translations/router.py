from fastapi import APIRouter

from app.modules.identity.deps import ActorDep
from app.modules.translations import service
from app.modules.translations.schemas import TranslationOut, TranslationRequest
from app.platform.db import SessionDep

translations = APIRouter(tags=["translations"])


@translations.post("/translations")
async def translate(body: TranslationRequest, actor: ActorDep, db: SessionDep) -> TranslationOut:
    """What Tamga wrote, in another language: read from what is kept, written once by a model."""
    return await service.translate(db, actor, language=body.language, texts=body.texts)
