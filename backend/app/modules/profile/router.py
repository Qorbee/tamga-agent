from fastapi import APIRouter

from app.modules.identity.deps import ActorDep
from app.modules.profile import service
from app.modules.profile.schemas import CompanyProfile, DomainAdd, IdentityChange, TaxIdSet
from app.platform.db import SessionDep

profile = APIRouter(tags=["profile"])


@profile.get("/company/profile")
async def get_profile(actor: ActorDep, db: SessionDep) -> CompanyProfile:
    return await service.get_profile(db, actor)


@profile.put("/company/profile/tax-id")
async def set_tax_id(body: TaxIdSet, actor: ActorDep, db: SessionDep) -> IdentityChange:
    return await service.set_tax_id(db, actor, body)


@profile.post("/company/profile/domains")
async def add_domain(body: DomainAdd, actor: ActorDep, db: SessionDep) -> IdentityChange:
    return await service.add_domain(db, actor, body)


@profile.delete("/company/profile/domains/{domain}")
async def remove_domain(domain: str, actor: ActorDep, db: SessionDep) -> CompanyProfile:
    return await service.remove_domain(db, actor, domain)
