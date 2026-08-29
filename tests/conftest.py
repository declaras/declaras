"""Infraestructura de pruebas: una app aislada por test, con conector falso."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from declaras.api.app import create_app
from declaras.api.auth.jwks import _Entrada
from declaras.config.settings import DianAdapterKind, Environment, Settings, StorageBackend
from tests.unit.auth.tokens_falsos import CORREO, EMISOR, EmisorFalso

# El proyecto de Supabase que finge el emisor de `tokens_falsos`. `EMISOR` es la URL del emisor
# (`.../auth/v1`), y los ajustes piden la del proyecto, que es su raiz.
SUPABASE_URL = EMISOR.removesuffix("/auth/v1")

# Un emisor por sesion de pruebas: generar una llave EC cuesta unos milisegundos y hacerlo por cada
# prueba multiplicaba eso por cientos. La llave no lleva estado entre pruebas —solo firma—, asi que
# compartirla no las acopla.
_emisor = EmisorFalso()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env=Environment.LOCAL,
        log_level="WARNING",
        supabase_url=SUPABASE_URL,
        contadores=[CORREO],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        storage_backend=StorageBackend.LOCAL,
        storage_local_root=tmp_path / "documents",
        # Sin llave, guardar un secreto revienta a proposito: las pruebas que guardan la
        # clave de la DIAN necesitan una, y ademas asi se ejercita el camino real.
        clave_de_cifrado="llave-de-pruebas",
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


def sembrar_llaves(app) -> None:
    """Deja la llave publica del emisor falso en la cache del contenedor.

    POR QUE SE SIEMBRA Y NO SE DEJA QUE LA BAJE. Bajarla es una peticion HTTPS al proyecto de
    Supabase: convertiria cada prueba de integracion en una prueba que depende de internet y del
    estado de un tercero. La bajada tiene sus propias pruebas en `tests/unit/auth`, con la logica de
    verdad —vencimiento, candado, rotacion, cupo— y sin red.

    `vence_en=inf` porque en una prueba el reloj no avanza lo suficiente para que importe, y un
    vencimiento realista solo agregaria una razon de falla intermitente.
    """
    cache = app.state.container.llaves
    assert cache is not None, "los ajustes de prueba configuran Supabase, deberia haber cache"
    cache._llaves = {_emisor.kid: _Entrada(llave=_emisor.jwk_publica, vence_en=float("inf"))}


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """Cliente autenticado como el contador habilitado.

    ═══ POR QUE UN TOKEN DE VERDAD Y NO UNA DEPENDENCIA SOBREESCRITA ═══

    FastAPI permite reemplazar `require_principal` con algo que devuelva un principal de mentira, y
    seria menos codigo. No se hace: eso saca al portero del camino que ejercitan las 1300 pruebas, y
    entonces nada comprueba que el portero este puesto: la prueba de que un endpoint pide credencial
    dejaria de probar algo el dia que alguien le quite la dependencia.

    Con un token firmado de verdad, cada peticion de cada prueba pasa por la verificacion completa
    —firma, emisor, audiencia, vencimiento, lista de contadores— igual que en produccion.
    """
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        sembrar_llaves(app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {_emisor.token()}"},
        ) as http:
            yield http


@pytest.fixture
async def client_sin_sesion(app) -> AsyncIterator[AsyncClient]:
    """Cliente SIN credencial, para probar que la puerta esta cerrada.

    Es una fixture propia y no `client` con la cabecera vaciada, porque httpx FUSIONA las cabeceras
    de la peticion con las del cliente: mandarle una credencial mala a `client` deja la buena puesta
    y la peticion entra. Fue exactamente lo que paso al quitar la llave de API — cuatro pruebas que
    decian "exige credencial" pasaban porque el cliente ya traia una valida.
    """
    async with app.router.lifespan_context(app):
        sembrar_llaves(app)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
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
    async with app.router.lifespan_context(app):
        sembrar_llaves(app)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {_emisor.token()}"},
        ) as http:
            yield http
