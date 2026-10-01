"""Consistent JSON error responses."""
import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("lms")


class ConflictError(Exception):
    """Business-rule conflict: duplicate value, no copies left, and similar."""


class NotFoundError(Exception):
    def __init__(self, what: str):
        super().__init__(f"{what} not found")


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        return JSONResponse(_body("not_found", str(exc)), status_code=404)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError):
        return JSONResponse(_body("conflict", str(exc)), status_code=409)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(_body("http_error", str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Invalid request", details), status_code=422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def _integrity(_: Request, exc: sqlite3.IntegrityError):
        logger.warning("Integrity error: %s", exc)
        return JSONResponse(_body("conflict", "Operation violates a data constraint"), status_code=409)

    @app.exception_handler(sqlite3.Error)
    async def _db(_: Request, exc: sqlite3.Error):
        logger.exception("Database error")
        return JSONResponse(_body("database_error", "A database error occurred"), status_code=500)
