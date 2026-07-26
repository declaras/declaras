"""Fabrica de la aplicacion FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from declaras import __version__
from declaras.api.container import Container
from declaras.api.errors import register_exception_handlers
from declaras.api.routers import cases, documents, documents_read, extractions, health
from declaras.config import Settings, get_settings
from declaras.observability import configure_logging

_DESCRIPTION = """
Conector DIAN de Declaras.

Servicio deterministico que autentica en el portal Muisca, descarga los insumos de la
declaracion de renta y los deja almacenados con su evidencia. Esta pensado para ser
consumido por el agente conversacional.

Contrato: las operaciones son asincronas. POST /v1/extractions encola y responde 202
con un job_id; el estado se consulta en GET /v1/extractions/{job_id} o llega al
callback_url. Los errores traen un `code` estable y el header X-Retryable.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container.build(settings)
        app.state.container = container
        await container.startup()
        try:
            yield
        finally:
            await container.shutdown()

    app = FastAPI(
        title="Declaras: conector DIAN",
        description=_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(extractions.router)
    app.include_router(documents.router)
    app.include_router(documents_read.router)
    app.include_router(cases.router)
    return app


app = create_app()
