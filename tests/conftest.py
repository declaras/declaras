"""Infraestructura de pruebas: una app aislada por test, con conector falso."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from declaras.api.app import create_app
from declaras.config.settings import DianAdapterKind, Environment, Settings, StorageBackend

API_KEY = "test-key"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env=Environment.LOCAL,
        log_level="WARNING",
        api_keys=[API_KEY],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        storage_backend=StorageBackend.LOCAL,
        storage_local_root=tmp_path / "documents",
        dian_adapter=DianAdapterKind.FAKE,
        dian_max_login_attempts=2,
        worker_enabled=True,
        worker_poll_interval_s=0.05,
        worker_max_attempts=1,
        dian_capture_evidence=True,
    )


@pytest.fixture
async def app(settings: Settings):
    """La app ya construida (sin arrancar): la comparten `client` y `container` para que
    ambos vean el mismo contenedor de dependencias."""
    return create_app(settings)


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": API_KEY},
        ) as http,
    ):
        yield http


@pytest.fixture
def container(app, client: AsyncClient):
    """El contenedor de dependencias de la app ya arrancada (`client` la arranca via
    lifespan), para pruebas que necesitan tocar un servicio directamente."""
    return app.state.container


async def wait_until(
    client: AsyncClient,
    job_id: str,
    predicate: Callable[[dict], bool],
    *,
    descripcion: str,
    timeout_s: float = 8.0,
) -> dict:
    """Sondea el job hasta que cumpla la condicion dada.

    Se espera por una condicion y no solo por un estado porque un job con reintentos pasa
    por FAILED de forma transitoria antes de volver a la cola: mirar solo el estado hace
    la prueba dependiente del azar.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/v1/extractions/{job_id}")).json()
        if predicate(last):
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"timeout esperando {descripcion}; ultimo estado: {last.get('status')} "
        f"(intentos: {last.get('attempts')})"
    )


async def wait_for_status(
    client: AsyncClient, job_id: str, *targets: str, timeout_s: float = 8.0
) -> dict:
    """Espera a que el job llegue a alguno de los estados pedidos."""
    return await wait_until(
        client,
        job_id,
        lambda job: job.get("status") in targets,
        descripcion=f"estado en {targets}",
        timeout_s=timeout_s,
    )


async def wait_for_final_failure(
    client: AsyncClient, job_id: str, *, attempts: int, timeout_s: float = 10.0
) -> dict:
    """Espera la falla definitiva, cuando ya se agotaron los reintentos."""
    return await wait_until(
        client,
        job_id,
        lambda job: job.get("status") == "FAILED" and job.get("attempts", 0) >= attempts,
        descripcion=f"falla definitiva con {attempts} intentos",
        timeout_s=timeout_s,
    )


@pytest.fixture
async def client_con_reintentos(settings: Settings) -> AsyncIterator[AsyncClient]:
    """Cliente con reintentos habilitados, para probar la ruta de reencolado."""
    settings = settings.model_copy(update={"worker_max_attempts": 2})
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": API_KEY},
        ) as http,
    ):
        yield http
