"""Sondas de salud. /health responde siempre; /health/ready valida dependencias."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from declaras import __version__
from declaras.api.deps import ContainerDep
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
