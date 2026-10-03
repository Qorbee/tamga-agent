"""Application factory.

Builds the FastAPI app, opens and closes the shared clients, maps domain errors, and mounts
routers. No business logic lives here; that belongs in ``app/modules/``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app import packs
from app.config import get_settings
from app.modules.assistant import router as assistant
from app.modules.books import router as books
from app.modules.conversations import router as conversations
from app.modules.documents import router as documents
from app.modules.identity import router as identity
from app.modules.linking import router as linking
from app.modules.parties import router as parties
from app.modules.projects import router as projects
from app.modules.translations import router as translations
from app.packs.logistics import router as logistics
from app.platform import isolated, tracing
from app.platform.db import dispose_db, init_db
from app.platform.errors import install_error_handlers
from app.platform.origin import OriginCheck
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db(str(get_settings().database_url))
    # Before any other thread starts: the forkserver starts with a narrowed environment.
    isolated.start()
    tracing.init_tracing("api")
    try:
        yield
    finally:
        # Sending what is batched waits on the network: never on the event loop.
        await asyncio.to_thread(tracing.shutdown_tracing)
        await dispose_db()


def operation_id(route: APIRoute) -> str:
    """``<tag>.<function name>``, so moving or re-prefixing a route does not rename the operation
    a generated frontend client is keyed on."""
    tag = str(route.tags[0]) if route.tags else "untagged"
    return f"{tag}.{route.name}"


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    packs.register_all()

    app = FastAPI(
        # A constant: the title is part of the committed openapi.json.
        title="tamga",
        version="0.1.0",
        lifespan=lifespan,
        generate_unique_id_function=operation_id,
    )
    # Added before CORS, so it runs inside it: CORS answers preflights before the check sees them.
    app.add_middleware(OriginCheck, allowed_origins=settings.cors_origin_list)
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
    # Each module's routers, one line each.
    app.include_router(identity.sessions)
    app.include_router(identity.me)
    app.include_router(identity.invites)
    app.include_router(projects.projects)
    app.include_router(projects.links)
    app.include_router(projects.proposals)
    app.include_router(linking.placements)
    app.include_router(parties.parties)
    app.include_router(translations.translations)
    app.include_router(conversations.messages)
    app.include_router(conversations.threads)
    app.include_router(documents.documents)
    app.include_router(books.invoices)
    app.include_router(assistant.chats)
    # Packs' routers: the core never imports a pack, the app factory does.
    app.include_router(logistics.shipments)

    return app


app = create_app()
