"""Sondas de salud. /health responde siempre; /health/ready valida dependencias."""

from __future__ import annotations

import os
import subprocess
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from declaras import __version__
from declaras.api.deps import ContainerDep
from declaras.api.origen import origen_de
from declaras.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


def _commit() -> str:
    """El commit que esta corriendo, para poder saber QUE se desplego sin adivinar.

    ═══ POR QUE HACE FALTA ═══

    `version` sale del pyproject y no cambia entre despliegues, asi que responder "0.1.0" no
    distingue el codigo de hoy del de hace dos semanas. Se perdio media hora averiguando si un
    commit habia llegado a produccion, probando por cuatro caminos indirectos (el esquema de
    OpenAPI, el enum de tipos de documento, endpoints que existen o no) y ninguno servia: todos
    contestaban sobre cosas que ya existian antes del commit en cuestion.

    ═══ POR QUE ES PUBLICO ═══

    El repositorio es publico, asi que el hash no revela nada que no se pueda leer en GitHub.
    Y el front ya publica el suyo en un `<meta>` del HTML por la misma razon.

    ═══ POR QUE NO ROMPE SI NO ESTA ═══

    Railway inyecta `RAILWAY_GIT_COMMIT_SHA` solo, pero en local y en las pruebas no existe. Se
    cae a leer git, y si tampoco hay, devuelve "desconocido": una sonda de salud que falla por no
    saber su propio commit seria peor que no tener el dato.
    """
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT_SHA")
    if sha:
        return sha[:7]
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            ).stdout.strip()
            or "desconocido"
        )
    except Exception:
        return "desconocido"


@router.get("/health", response_model=HealthResponse)
async def health(container: ContainerDep) -> HealthResponse:
    settings = container.settings
    return HealthResponse(
        status="ok",
        version=__version__,
        env=settings.env.value,
        dian_adapter=settings.dian_adapter.value,
        worker_enabled=settings.worker_enabled,
        commit=_commit(),
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
        commit=_commit(),
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
        origen=origen_de(request, saltos_de_confianza=container.settings.proxies_de_confianza),
        saltos_de_confianza=container.settings.proxies_de_confianza,
        valores_reenviados=len([p for p in reenviado.split(",") if p.strip()]),
    )
