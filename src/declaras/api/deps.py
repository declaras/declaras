"""Dependencias de FastAPI: acceso al contenedor y autenticacion."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from declaras.api.container import Container
from declaras.domain.errors import DeclarasError
from declaras.services.extraction import ExtractionService

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class UnauthorizedError(DeclarasError):
    code = "UNAUTHORIZED"
    http_status = 401
    default_message = "Falta la llave de API o no es válida."


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def require_api_key(
    container: ContainerDep,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
) -> str:
    if not api_key or api_key not in container.settings.api_keys:
        raise UnauthorizedError()
    return api_key


def get_extraction_service(container: ContainerDep) -> ExtractionService:
    return container.extraction


ApiKeyDep = Annotated[str, Depends(require_api_key)]
ExtractionDep = Annotated[ExtractionService, Depends(get_extraction_service)]
