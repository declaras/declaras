"""El portero de la API, visto desde afuera: quién entra por HTTP y quién no.

Las pruebas de `tests/unit/auth/test_token.py` cubren la verificación del token. Acá se prueba lo
que **solo se ve armado**: que las dos credenciales convivan durante la migración, cuál gana
cuando llegan las dos, y que nada de esto abra un camino que antes no existía.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from declaras.api.app import create_app
from declaras.api.auth.jwks import _Entrada
from declaras.api.container import Container
from tests.conftest import API_KEY
from tests.unit.auth.tokens_falsos import CORREO, EmisorFalso

pytestmark = pytest.mark.anyio

RUTA = "/v1/clients"
SUPABASE_URL = "https://proyecto.supabase.co"


@pytest.fixture
def emisor() -> EmisorFalso:
    return EmisorFalso()


@pytest.fixture
def app_con_auth(settings, emisor):
    """La app con auth de personas configurado. Las llaves las siembra `pedir`."""
    ajustes = settings.model_copy(update={"supabase_url": SUPABASE_URL, "contadores": [CORREO]})
    return create_app(ajustes)


async def pedir(app, emisor: EmisorFalso | None = None, **cabeceras) -> int:
    """Una petición contra la app, con las llaves públicas ya en caché si se pasa el emisor.

    El contenedor solo existe DESPUÉS del lifespan, así que la caché se siembra acá adentro y no
    en la fixture — que fue el primer intento y falló con `State has no attribute 'container'`.

    Se siembra en vez de dejar que salga a la red: la bajada la prueban las pruebas de la caché, y
    una prueba de integración que dependa de internet falla los martes por razones ajenas.
    """
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        if emisor is not None:
            cache = app.state.container.llaves
            assert cache is not None, "con supabase_url configurado tiene que haber caché"
            cache._llaves = {emisor.kid: _Entrada(llave=emisor.jwk_publica, vence_en=float("inf"))}
        return (await http.get(RUTA, headers=cabeceras)).status_code


# ─────────────────────────── sin credencial, nada ───────────────────────────


async def test_sin_credencial_no_se_entra(app_con_auth, emisor):
    assert await pedir(app_con_auth, emisor) == 401


async def test_una_llave_que_no_existe_no_entra(app_con_auth, emisor):
    assert await pedir(app_con_auth, emisor, **{"X-API-Key": "inventada"}) == 401


# ─────────────────────────── la migración por etapas ───────────────────────────


async def test_la_llave_de_servicio_sigue_sirviendo(app_con_auth, emisor):
    """La etapa 1 tiene que quedar desplegable.

    Hoy el front habla por el proxy con la llave, y los scripts y las pruebas entran así. Si el
    auth de personas hubiera reemplazado la llave de golpe, el despliegue quedaría roto hasta que
    el front terminara de cambiar — y esa ventana es justo donde se toman atajos.
    """
    assert await pedir(app_con_auth, emisor, **{"X-API-Key": API_KEY}) == 200


async def test_un_token_de_persona_habilitada_entra(app_con_auth, emisor):
    assert await pedir(app_con_auth, emisor, Authorization=f"Bearer {emisor.token()}") == 200


async def test_un_token_de_alguien_que_no_esta_en_la_lista_da_403(app_con_auth, emisor):
    token = emisor.token(email="cualquiera@gmail.com")
    assert await pedir(app_con_auth, emisor, Authorization=f"Bearer {token}") == 403


async def test_un_token_invalido_da_401(app_con_auth, emisor):
    token = emisor.token(vence_en=-10)
    assert await pedir(app_con_auth, emisor, Authorization=f"Bearer {token}") == 401


# ─────────────────────────── cuál gana cuando llegan las dos ───────────────────────────


async def test_con_token_y_llave_manda_el_token(app_con_auth, emisor):
    """El token es más específico: dice QUIÉN.

    Si la llave le ganara, una persona identificada dejaría un rastro que dice "servicio" — y el
    rastro es la razón de ser de todo esto.
    """
    token = emisor.token()
    assert (
        await pedir(app_con_auth, emisor, Authorization=f"Bearer {token}", **{"X-API-Key": API_KEY})
        == 200
    )


async def test_un_token_malo_no_se_salva_con_una_llave_buena(app_con_auth, emisor):
    """El caso que convertiría la llave en un escape.

    Si al fallar el token se cayera a la llave, cualquiera con la llave del proxy podría mandar
    tokens ajenos y entrar de todas formas — y el 403 de la lista de permitidos no valdría nada.
    Quien manda un token espera que se juzgue el token.
    """
    ajeno = emisor.token(email="cualquiera@gmail.com")
    assert (
        await pedir(app_con_auth, emisor, Authorization=f"Bearer {ajeno}", **{"X-API-Key": API_KEY})
        == 403
    )


# ─────────────────────────── sin configurar, cerrado ───────────────────────────


async def test_sin_proyecto_configurado_un_token_no_entra(settings, emisor):
    """El estado de hoy en producción: no hay Supabase configurado todavía.

    Un token no se puede evaluar sin proyecto contra el que validarlo, así que se rechaza. Lo que
    NO puede pasar es que se ignore el token y se deje pasar por otra vía: el peor resultado sería
    aceptar un token que nadie verificó.
    """
    # Se comprueba sobre el contenedor construido a mano: `app.state.container` no existe hasta
    # que corre el lifespan, y lo que importa afirmar es el porqué —sin proyecto no hay llaves que
    # cachear— además del 401.
    assert Container.build(settings).llaves is None
    app = create_app(settings)
    assert await pedir(app, Authorization=f"Bearer {emisor.token()}") == 401


async def test_con_proyecto_pero_sin_lista_nadie_entra_con_token(settings, emisor):
    """Media configuración es la trampa.

    Con proyecto y sin lista de contadores, cualquier token válido del proyecto entraría — y con
    el registro público de Supabase encendido, eso es cualquiera en internet. Por eso
    `auth_de_usuario_activo` exige las dos cosas y esto rebota.
    """
    ajustes = settings.model_copy(update={"supabase_url": SUPABASE_URL, "contadores": []})
    app = create_app(ajustes)
    assert await pedir(app, Authorization=f"Bearer {emisor.token()}") == 401
