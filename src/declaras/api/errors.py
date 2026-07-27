"""Traduccion de errores del dominio a respuestas HTTP.

El cuerpo de error siempre tiene la misma forma (code, message, retryable, details) y ademas
viaja el header X-Retryable, para que el agente pueda decidir sin parsear.

QUE SE DEVUELVE DE UN ERROR DE VALIDACION Y QUE NO

De cada error de pydantic se conservan solo el tipo, el campo y el mensaje. Se descartan dos
cosas a proposito:

  `input`  es lo que mando el cliente, devuelto tal cual. Cuando falla la validacion de un
           campo suelto es inofensivo, pero cuando falla una regla del modelo completo pydantic
           pone ahi el cuerpo entero, y en esta API algunos cuerpos llevan la clave de la DIAN.
           Ninguna respuesta de error tiene por que repetir lo que le mandaron.
  `ctx`    trae el objeto de la excepcion original. Ademas de no aportar nada a quien consume la
           API, no es serializable a JSON: intentar devolverlo hacia que una regla del modelo
           terminara en un 500 en vez del 422 que corresponde.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from declaras.domain.errors import DeclarasError, ValidationError
from declaras.observability import get_logger

log = get_logger(__name__)


_MAX_ERRORES = 5


def _detalles(errores: Sequence[Any]) -> list[dict[str, str]]:
    """Lo que se puede devolver de un error de validacion: donde fallo y por que."""
    return [
        {
            "campo": ".".join(str(parte) for parte in error.get("loc", ()) if parte != "body"),
            "problema": str(error.get("msg", "")),
        }
        for error in errores[:_MAX_ERRORES]
    ]


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
            ValidationError("Los datos enviados no son válidos.", errors=_detalles(exc.errors()))
        )

    @app.exception_handler(PydanticValidationError)
    async def _domain_validation_error(
        _request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        """Reglas de validacion del dominio: son error del cliente, no del servidor."""
        detalles = _detalles(exc.errors())
        resumen = "; ".join(d["problema"] for d in detalles if d["problema"])
        return _response(ValidationError(resumen or None, errors=detalles))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_error", error=str(exc)[:200])
        return _response(DeclarasError())
