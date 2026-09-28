from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import Company, CompanyKind
from app.modules.identity import service
from app.modules.identity.models import User
from app.platform import storage
from app.platform.clock import utcnow
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def test_an_expired_sandbox_goes_with_its_files_and_guest(
    client: httpx.AsyncClient, db: AsyncSession, storage_bucket: None
) -> None:
    sandbox_id = await guest_client(client)
    upload = await client.post("/documents", params={"filename": "a.pdf"}, content=b"guest file")
    key = storage.content_key(sandbox_id, b"guest file")
    pilot = await companies.create_company(db, name="Acme", kind=CompanyKind.PILOT)
    live = await guest_client(client)  # another sandbox, still within its lifetime
    sandbox = await db.get(Company, sandbox_id)
    assert sandbox is not None and upload.status_code == 201
    sandbox.expires_at = utcnow() - timedelta(minutes=1)
    await db.commit()

    assert await service.expire_sandboxes() == 1

    db.expunge_all()
    remaining = set(await db.scalars(select(Company.id)))
    assert remaining == {pilot.id, live}
    assert len((await db.scalars(select(User).where(User.is_guest))).all()) == 1
    with pytest.raises(Exception, match="NoSuchKey|Not Found|404"):
        await storage.get(key)
