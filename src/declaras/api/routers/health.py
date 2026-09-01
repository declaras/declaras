"""Sondas de salud. /health responde siempre; /health/ready valida dependencias."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from declaras import __version__
from declaras.api.deps import ContainerDep
from declaras.api.origen import origen_de
from declaras.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(container: ContainerDep) -> HealthResponse:
    settings = container.settings
    return HealthResponse(
        status="ok",
        version=__version__,
        env=settings.env.value,
        dian_adapter=settings.dian_adapter.value,
        worker_enabled=settings.worker_enabled,
    )


@router.get("/health/ready", response_model=HealthResponse)
async def ready(container: ContainerDep) -> HealthResponse:
    status: Literal["ok", "degraded"] = "ok"
    try:
        async with container.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        status = "degraded"
    settings = container.settings
    return HealthResponse(
        status=status,
        version=__version__,
        env=settings.env.value,
        dian_adapter=settings.dian_adapter.value,
        worker_enabled=settings.worker_enabled,
    )


class OrigenResponse(BaseModel):
    """Como ve este servicio a quien le pide, que es de lo que depende el limite por origen."""

    origen: str
    saltos_de_confianza: int
    valores_reenviados: int


@router.get("/health/origen", response_model=OrigenResponse)
async def origen(request: Request, container: ContainerDep) -> OrigenResponse:
    """De donde cree el servidor que viene esta peticion.

    ═══ POR QUE ES UN ENDPOINT Y NO UNA LINEA DE LOG ═══

    El limite por origen depende de cuantos proxies hay delante, y ese numero no se puede
    deducir leyendo codigo: depende de como este montada la infraestructura HOY. Equivocarlo no
    falla, cuenta mal —o se cuenta a todo el mundo junto, o no se cuenta a nadie— y en los dos
    casos el sintoma aparece semanas despues y no se parece a su causa.

    Con esto se verifica en una llamada, cada vez que algo cambie delante del servicio.

    ═══ POR QUE ES PUBLICO ═══

    A quien pregunta se le devuelve SU PROPIA direccion, que ya conoce. No revela nada de otro
    visitante ni de la red interna. Y saber cuantos saltos hay no ayuda a saltarse el limite,
    porque los valores se cuentan desde el extremo que escribe el proxy, no desde el que
    escribe quien llama.
    """
    reenviado = request.headers.get("x-forwarded-for", "")
    return OrigenResponse(
        origen=origen_de(
            request, saltos_de_confianza=container.settings.proxies_de_confianza
        ),
        saltos_de_confianza=container.settings.proxies_de_confianza,
        valores_reenviados=len([p for p in reenviado.split(",") if p.strip()]),
    )
