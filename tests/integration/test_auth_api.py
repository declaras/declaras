"""El portero de la API, visto desde afuera: quién entra por HTTP y quién no.

`tests/unit/auth/test_token.py` cubre la verificación del token. Acá se prueba lo que **solo se ve
armado**: que no quede ningún camino alterno, que la puerta vieja esté cerrada, y que un despliegue
sin configurar falle del lado correcto.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from declaras.api.app import create_app
from declaras.api.auth.jwks import _Entrada
from declaras.api.container import Container
from tests.conftest import SUPABASE_URL
from tests.unit.auth.tokens_falsos import EmisorFalso

pytestmark = pytest.mark.anyio

RUTA = "/v1/clients"


@pytest.fixture
def emisor() -> EmisorFalso:
    return EmisorFalso()


@pytest.fixture
def app_con_auth(settings, emisor):
    """La app configurada, pero con un emisor propio de esta prueba.

    No reusa el de `conftest` para que cada caso pueda firmar tokens raros sin ensuciar el emisor
    compartido que usan las otras 1300 pruebas.
    """
    app = create_app(settings)
    app.state.emisor_de_prueba = emisor
    return app


async def pedir(app, sembrar=True, **cabeceras):
    async with app.router.lifespan_context(app):
        if sembrar:
            cache = app.state.container.llaves
            emisor = app.state.emisor_de_prueba
            cache._llaves = {emisor.kid: _Entrada(llave=emisor.jwk_publica, vence_en=float("inf"))}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            return await http.get(RUTA, headers=cabeceras)


# ─────────────────────────── una sola puerta ───────────────────────────


async def test_sin_credencial_no_se_entra(app_con_auth):
    respuesta = await pedir(app_con_auth)
    assert respuesta.status_code == 401
    assert respuesta.json()["code"] == "UNAUTHORIZED"


async def test_un_token_de_persona_habilitada_entra(app_con_auth, emisor):
    respuesta = await pedir(app_con_auth, Authorization=f"Bearer {emisor.token()}")
    assert respuesta.status_code == 200


async def test_la_llave_de_api_ya_no_sirve(app_con_auth):
    """Regresión: la puerta vieja está cerrada.

    Existió una `X-API-Key` compartida, y con ella un proxy público que la aplicaba para cualquiera
    —una URL sin autenticar que devolvía cédulas y correos. Se quitó. Esta prueba existe para que no
    vuelva por descuido: cualquier reintroducción de un camino que no sea un token de persona la
    rompe.
    """
    respuesta = await pedir(app_con_auth, **{"X-API-Key": "cualquier-cosa"})
    assert respuesta.status_code == 401


async def test_un_token_de_alguien_que_no_esta_en_la_lista_da_403(app_con_auth, emisor):
    respuesta = await pedir(
        app_con_auth, Authorization=f"Bearer {emisor.token(email='x@gmail.com')}"
    )
    assert respuesta.status_code == 403
    assert respuesta.json()["code"] == "NO_AUTORIZADO"


async def test_un_token_vencido_da_401(app_con_auth, emisor):
    respuesta = await pedir(app_con_auth, Authorization=f"Bearer {emisor.token(vence_en=-10)}")
    assert respuesta.status_code == 401
    assert respuesta.json()["code"] == "TOKEN_INVALIDO"


# ─────────────────────────── sin configurar, cerrado ───────────────────────────


async def test_sin_proyecto_configurado_la_api_no_deja_pasar_a_nadie(settings, emisor):
    """Desplegar sin las variables deja la API INSERVIBLE, no permisiva.

    Es el lado correcto en el que fallar, y es una decisión: no hay respaldo "por si el auth no está
    configurado", porque un respaldo así es exactamente la puerta que se acabó de cerrar.

    Se responde 503 y no 401: a quien pregunta no le falta una credencial, es este despliegue el que
    no puede validar ninguna. Un 401 mandaría a la consola a pedir que entre de nuevo, en un bucle
    que nadie resuelve escribiendo bien la clave.
    """
    ajustes = settings.model_copy(update={"supabase_url": None, "contadores": []})
    assert Container.build(ajustes).llaves is None
    app = create_app(ajustes)
    app.state.emisor_de_prueba = emisor

    respuesta = await pedir(app, sembrar=False, Authorization=f"Bearer {emisor.token()}")
    assert respuesta.status_code == 503
    assert respuesta.json()["code"] == "AUTH_NO_CONFIGURADO"


async def test_con_proyecto_pero_sin_lista_tampoco(settings, emisor):
    """Media configuración es la trampa.

    Con proyecto y sin lista de contadores, cualquier token válido de ese proyecto de Supabase
    entraría — y con el registro público encendido, eso es cualquiera en internet.
    """
    ajustes = settings.model_copy(update={"supabase_url": SUPABASE_URL, "contadores": []})
    app = create_app(ajustes)
    app.state.emisor_de_prueba = emisor

    respuesta = await pedir(app, Authorization=f"Bearer {emisor.token()}")
    assert respuesta.status_code == 503


# ─────────────────────────── CORS ───────────────────────────


async def test_sin_origenes_configurados_ningun_navegador_puede_llamar(settings, emisor):
    """El default es cerrado.

    Sin `cors_origins` no se instala el middleware, así que la respuesta no trae el permiso y el
    navegador descarta el resultado. Que el default sea vacío —y no `*`— es lo que evita que
    cualquier página que alguien visite pueda llamar a esta API con la sesión del contador.
    """
    app = create_app(settings)
    app.state.emisor_de_prueba = emisor
    respuesta = await pedir(
        app, Authorization=f"Bearer {emisor.token()}", Origin="https://sitio-ajeno.com"
    )
    assert "access-control-allow-origin" not in respuesta.headers


async def test_el_origen_configurado_si_recibe_permiso(settings, emisor):
    ajustes = settings.model_copy(update={"cors_origins": ["https://declaras.co"]})
    app = create_app(ajustes)
    app.state.emisor_de_prueba = emisor
    respuesta = await pedir(
        app, Authorization=f"Bearer {emisor.token()}", Origin="https://declaras.co"
    )
    assert respuesta.headers.get("access-control-allow-origin") == "https://declaras.co"
    # Sin esto el navegador no deja que el JavaScript lea la respuesta, aunque llegue bien.
    assert respuesta.headers.get("access-control-allow-credentials") == "true"


async def test_un_origen_que_no_esta_en_la_lista_no_recibe_permiso(settings, emisor):
    ajustes = settings.model_copy(update={"cors_origins": ["https://declaras.co"]})
    app = create_app(ajustes)
    app.state.emisor_de_prueba = emisor
    respuesta = await pedir(
        app, Authorization=f"Bearer {emisor.token()}", Origin="https://declaras.co.atacante.com"
    )
    # El sufijo del atacante contiene el dominio bueno: una comparacion por `startswith` o por
    # `in` lo dejaria pasar. Se afirma que no.
    assert respuesta.headers.get("access-control-allow-origin") != (
        "https://declaras.co.atacante.com"
    )
