"""El canje de sesion por token es el acceso a la API: si cambia, se rompe todo."""

from __future__ import annotations

import base64

import httpx
import pytest

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.adapters.dian.rest.api_client import DianApiClient, build_digest
from declaras.domain.errors import (
    DianDocumentUnavailableError,
    DianError,
    DianPortalUnavailableError,
    DianRateLimitedError,
    DianSessionExpiredError,
)

PORTAL = "https://muisca.dian.gov.co"
COOKIE = "N_2_313330303130_6a653a17_N_4a75"


def test_el_digest_es_la_cookie_en_base64():
    assert base64.b64decode(build_digest(COOKIE)).decode() == COOKIE


async def test_autentica_y_consulta_con_los_headers_que_la_api_exige():
    vistos: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            vistos["authorization"] = request.headers["authorization"]
            return httpx.Response(200, json={"idToken": "jwt-de-prueba", "expireIn": 3600})
        vistos.update({k: v for k, v in request.headers.items()})
        return httpx.Response(200, json={"anios": [2025, 2024]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        http.cookies.set("DIAN-MUISCA", COOKIE, domain="muisca.dian.gov.co")
        api = DianApiClient(http, portal_url=PORTAL)
        data = await api.get_json(DIAN_API.renta_years)

    assert data == {"anios": [2025, 2024]}
    assert vistos["authorization"] == "Bearer jwt-de-prueba"
    assert vistos["clientid"] == DIAN_API.client_id, "sin clientid la API rechaza"
    assert vistos["x-request-id"] == vistos["etag"]


async def test_sin_cookie_de_sesion_no_hay_token():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as http:
        api = DianApiClient(http, portal_url=PORTAL)
        with pytest.raises(DianSessionExpiredError):
            await api.authenticate()


async def test_token_rechazado_se_reporta_como_sesion_expirada():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"mensaje": "no"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        http.cookies.set("DIAN-MUISCA", COOKIE, domain="muisca.dian.gov.co")
        api = DianApiClient(http, portal_url=PORTAL)
        with pytest.raises(DianSessionExpiredError):
            await api.authenticate()


# ─────── que hace la API cuando no responde 200 ───────
#
# Paso con un contribuyente real que nunca habia declarado: la API responde 404 al preguntar por
# su declaracion presentada, y como eso salia como una excepcion de la libreria HTTP y no como
# una falla de la DIAN, la extraccion entera se cayo con "INTERNAL_ERROR" y el texto crudo de
# httpx. Quien la lanzo no supo ni si la clave estaba bien.


async def _consultar_con_estado(codigo: int):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt", "expireIn": 3600})
        return httpx.Response(codigo, json={"mensaje": "algo"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        http.cookies.set("DIAN-MUISCA", COOKIE, domain="muisca.dian.gov.co")
        await DianApiClient(http, portal_url=PORTAL).get_json(DIAN_API.renta_years)


@pytest.mark.parametrize(
    ("codigo", "esperado", "por_que"),
    [
        (404, DianDocumentUnavailableError, "no tener un documento no es una falla del sistema"),
        (401, DianSessionExpiredError, "hay que volver a autenticar"),
        (429, DianRateLimitedError, "hay que esperar, no reintentar de una"),
        (503, DianPortalUnavailableError, "es la DIAN la que esta caida, no nosotros"),
        (400, DianError, "sigue siendo un problema con la DIAN, no un error interno"),
    ],
)
async def test_cada_respuesta_de_la_api_se_traduce_a_una_falla_del_dominio(
    codigo, esperado, por_que
):
    with pytest.raises(esperado):
        await _consultar_con_estado(codigo)


async def test_ninguna_respuesta_de_la_api_sale_como_error_interno():
    """Es la regla de fondo: un problema con la DIAN nunca puede reportarse como un error
    nuestro, porque manda a buscar la causa al lado equivocado."""
    for codigo in (400, 401, 403, 404, 409, 429, 500, 502, 503):
        with pytest.raises(DianError) as capturado:
            await _consultar_con_estado(codigo)
        assert capturado.value.code != "INTERNAL_ERROR"
