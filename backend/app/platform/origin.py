"""Refuses a state-changing request sent from a page that is not ours.

The session cookie is ``SameSite=Lax`` and request bodies are JSON only, and a browser always sends
``Origin`` on a cross-site POST; checking it against the allowed origins covers CSRF without a token
(ADR-0004). A request with no ``Origin`` is not from a browser page, so it carries no cookie by
accident and passes. Written as plain ASGI so streamed responses pass through untouched.
"""

from collections.abc import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class OriginCheck:
    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in _SAFE_METHODS:
            origin = dict(scope["headers"]).get(b"origin")
            if origin is not None and origin.decode("latin-1") not in self.allowed:
                response = JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "origin_not_allowed",
                            "message": "This request came from a page that may not change data",
                        }
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
