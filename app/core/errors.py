"""Domain errors and their mapping to HTTP responses."""

import logging

from asyncpg.exceptions import LockNotAvailableError, QueryCanceledError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

log = logging.getLogger(__name__)


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class InsufficientStockError(ConflictError):
    pass


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, **exc.details},
        )

    @app.exception_handler(PoolTimeoutError)
    async def _pool_exhausted(_: Request, exc: PoolTimeoutError) -> JSONResponse:
        # Back-pressure: the pool is saturated. Tell the client to retry
        # instead of letting the request hang.
        log.warning("DB pool exhausted: %s", exc)
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={"detail": "Database busy, please retry"},
        )

    @app.exception_handler(DBAPIError)
    async def _dbapi(_: Request, exc: DBAPIError) -> JSONResponse:
        cause = getattr(exc.orig, "__cause__", None) or exc.orig
        if isinstance(cause, LockNotAvailableError):
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "1"},
                content={"detail": "Product is under heavy contention, please retry"},
            )
        if isinstance(cause, QueryCanceledError):
            return JSONResponse(status_code=504, content={"detail": "Query timed out"})
        log.exception("Unhandled database error")
        return JSONResponse(status_code=500, content={"detail": "Database error"})
