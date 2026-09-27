"""Domain errors, and the one place they become HTTP responses.

Services raise these; they never raise ``HTTPException``. The handler installed by
``install_error_handlers`` turns each into ``{"error": {"code", "message"}}`` with the class's
status, so the same domain error is the same status everywhere. ``code`` is the stable,
machine-readable part a client branches on; ``message`` is shown to people and must never carry a
secret, token or full PII field.
"""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str | None = None, *, field: str | None = None) -> None:
        self.message = message or self.code
        # The input at fault, as the client names it, so a form can highlight it.
        self.field = field
        super().__init__(self.message)


class BadRequest(DomainError):
    status = HTTPStatus.BAD_REQUEST
    code = "bad_request"


class NotAuthenticated(DomainError):
    status = HTTPStatus.UNAUTHORIZED
    code = "not_authenticated"


class PermissionDenied(DomainError):
    status = HTTPStatus.FORBIDDEN
    code = "permission_denied"


class NotFound(DomainError):
    status = HTTPStatus.NOT_FOUND
    code = "not_found"


class Conflict(DomainError):
    status = HTTPStatus.CONFLICT
    code = "conflict"


def install_error_handlers(app: FastAPI) -> None:
    async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, DomainError):  # registered for DomainError only; narrows the type
            raise exc
        error: dict[str, str] = {"code": exc.code, "message": exc.message}
        if exc.field:
            error["field"] = exc.field
        return JSONResponse(status_code=int(exc.status), content={"error": error})

    app.add_exception_handler(DomainError, handle_domain_error)
