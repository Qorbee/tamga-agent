"""Liveness and readiness probes.

``/health`` touches nothing, so it answers while a dependency is down — an orchestrator restarting
the process would not fix Postgres. ``/health/ready`` checks every backing service and answers 503
when one fails, which is what a load balancer should route on.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.platform import storage
from app.platform.db import SessionDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ok", "unavailable"]
    database: bool
    storage: bool


@router.get("/health")
async def health() -> Health:
    return Health(status="ok")


@router.get(
    "/health/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def ready(session: SessionDep, response: Response) -> Readiness:
    try:
        await session.execute(text("SELECT 1"))
        database = True
    except Exception:
        logger.exception("readiness: database check failed")
        database = False

    files = await storage.ready()
    if not files:
        logger.warning("readiness: file storage is not configured or not reachable")

    ok = database and files
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if ok else "unavailable", database=database, storage=files)
