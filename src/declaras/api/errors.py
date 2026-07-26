"""Traduccion de errores del dominio a respuestas HTTP.

El cuerpo de error siempre tiene la misma forma (code, message, retryable, details) y
ademas viaja el header X-Retryable, para que el agente pueda decidir sin parsear.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from declaras.domain.errors import DeclarasError, ValidationError
from declaras.observability import get_logger

log = get_logger(__name__)


def _response(error: DeclarasError) -> JSONResponse:
    return JSONResponse(
        status_code=error.http_status,
        content=error.to_payload(),
        headers={"X-Retryable": "true" if error.retryable else "false"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DeclarasError)
    async def _declaras_error(_request: Request, exc: DeclarasError) -> JSONResponse:
        if exc.http_status >= 500:
            log.error("api.error", code=exc.code, message=exc.message)
        else:
            log.info("api.client_error", code=exc.code)
        return _response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(
            ValidationError("los datos enviados no son validos", errors=exc.errors()[:5])
        )

    @app.exception_handler(PydanticValidationError)
    async def _domain_validation_error(
        _request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        """Reglas de validacion del dominio: son error del cliente, no del servidor."""
        messages = [err.get("msg", "") for err in exc.errors()[:5]]
        return _response(ValidationError("; ".join(m for m in messages if m), errors=messages))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_error", error=str(exc)[:200])
        return _response(DeclarasError())
