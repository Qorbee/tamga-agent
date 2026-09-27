"""Application factory.

Builds the FastAPI app, opens and closes the shared clients, maps domain errors, and mounts
routers. No business logic lives here; that belongs in ``app/modules/``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.config import get_settings
from app.platform.db import dispose_db, init_db
from app.platform.errors import install_error_handlers
from app.platform.redis_client import close_redis, init_redis
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db(str(settings.database_url))
    init_redis(str(settings.redis_url))
    try:
        yield
    finally:
        await close_redis()
        await dispose_db()


def operation_id(route: APIRoute) -> str:
    """``<tag>.<function name>``, so moving or re-prefixing a route does not rename the operation
    a generated frontend client is keyed on."""
    tag = str(route.tags[0]) if route.tags else "untagged"
    return f"{tag}.{route.name}"


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        generate_unique_id_function=operation_id,
    )
    if settings.cors_origin_list:
        # Explicit origins, never "*": credentials are allowed, and browsers refuse the wildcard
        # with credentials anyway.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    install_error_handlers(app)

    app.include_router(health.router)
    # Mount each module's router here, one line per module:
    #   from app.modules.orders.router import router as orders_router
    #   app.include_router(orders_router)

    return app


app = create_app()
