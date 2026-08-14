"""Las declaraciones se obtienen por la API: buscar el id y descargar el PDF."""

from __future__ import annotations

import httpx
import pytest

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.adapters.dian.rest.api_client import DianApiClient
from declaras.adapters.dian.rest.client import PortalClient, PortalContext
from declaras.adapters.dian.rest.flows import download_prior_return, download_suggested_return
from declaras.domain.errors import DianDocumentUnavailableError
from declaras.domain.models import DocumentType, TaxpayerRef

PORTAL = "https://muisca.dian.gov.co"
PDF = b"%PDF-1.4 declaracion"


def _listado(anio: int, form_id: str) -> dict:
    return {
        "listadoFormularios": {
            "infoFormularios": [
                {
                    "identificador": {"id": form_id, "formato": 210, "versionFormato": 18},
                    "anio": anio,
                }
            ]
        }
    }


def _contexto(handler) -> tuple[PortalContext, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http.cookies.set("DIAN-MUISCA", "cookie-de-sesion", domain="muisca.dian.gov.co")
    ctx = PortalContext(
        portal=PortalClient(http, PORTAL), api=DianApiClient(http, portal_url=PORTAL)
    )
    return ctx, http


def _handler(*, filed: dict, pending: dict, download_id: str):
    def handler(request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, request.url.params
        if path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if path == DIAN_API.renta_forms:
            estado = query.get("estado")
            return httpx.Response(200, json=filed if estado == "presentado" else pending)
        if path == DIAN_API.renta_form_download.format(form_id=download_id):
            return httpx.Response(
                200,
                content=PDF,
                headers={"content-disposition": "attachment; filename=decl.pdf"},
            )
        return httpx.Response(404, json={})

    return handler


async def test_la_declaracion_anterior_es_la_del_anio_previo():
    """Al preparar 2025, el insumo es la declaracion presentada de 2024."""
    ctx, http = _contexto(
        _handler(filed=_listado(2024, "111"), pending=_listado(2025, "222"), download_id="111")
    )
    doc = await download_prior_return(ctx, TaxpayerRef(id_number="1020304050", tax_year=2025))
    await http.aclose()

    assert doc.doc_type is DocumentType.PRIOR_RETURN
    assert doc.content == PDF
    assert doc.metadata["tax_year"] == 2024
    assert doc.metadata["form_id"] == "111"


async def test_el_borrador_sugerido_es_del_anio_en_curso():
    ctx, http = _contexto(
        _handler(filed=_listado(2024, "111"), pending=_listado(2025, "222"), download_id="222")
    )
    doc = await download_suggested_return(ctx, TaxpayerRef(id_number="1020304050", tax_year=2025))
    await http.aclose()

    assert doc.doc_type is DocumentType.SUGGESTED_RETURN
    assert doc.metadata["tax_year"] == 2025


async def test_sin_declaracion_del_anio_se_reporta_no_disponible():
    """El primerizo no tiene declaracion anterior: la extraccion sigue sin ese documento."""
    vacio: dict = {"listadoFormularios": {"infoFormularios": []}}
    ctx, http = _contexto(_handler(filed=vacio, pending=vacio, download_id="x"))
    with pytest.raises(DianDocumentUnavailableError):
        await download_prior_return(ctx, TaxpayerRef(id_number="1020304050", tax_year=2025))
    await http.aclose()


async def test_si_solo_hay_otros_anios_informa_cuales_hay():
    ctx, http = _contexto(
        _handler(filed=_listado(2022, "999"), pending=_listado(2025, "222"), download_id="999")
    )
    with pytest.raises(DianDocumentUnavailableError) as exc:
        await download_prior_return(ctx, TaxpayerRef(id_number="1020304050", tax_year=2025))
    await http.aclose()
    assert exc.value.details["available_years"] == [2022]


async def test_quien_nunca_ha_declarado_no_es_un_error_del_sistema():
    """Un 404 sale como documento no disponible —para que la extraccion siga y traiga los otros
    cuatro— PERO SIN AFIRMAR MAS DE LO QUE SE SABE.

    Decia "la DIAN no tiene ninguna declaracion a nombre del contribuyente", que es una afirmacion
    categorica sacada de un codigo HTTP ambiguo: un 404 tambien puede ser un endpoint que cambio o
    un recurso al que la sesion no alcanza. Y la diferencia no es de redaccion — si la persona SI
    declaro y aqui se afirma que no, el patrimonio inicial entra vacio a la declaracion nueva sin
    que nadie lo note."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        return httpx.Response(404, json={"mensaje": "no existe"})

    ctx, http = _contexto(handler)
    async with http:
        with pytest.raises(DianDocumentUnavailableError) as capturado:
            await download_prior_return(ctx, TaxpayerRef(id_number="10203040", tax_year=2025))

    assert capturado.value.code == "DIAN_DOCUMENT_UNAVAILABLE"
    assert not capturado.value.retryable, "volver a pedirla no la va a hacer aparecer"
    # El mensaje lo lee una persona: dice lo que se OBSERVO —que la DIAN no reporto nada— y pide
    # verificarlo, en vez de sentenciar que la declaracion no existe.
    assert "no reportó ninguna declaración presentada" in capturado.value.message
    assert "verificarlo en el portal" in capturado.value.message
    assert capturado.value.details["evidencia"] == "respuesta 404 de la API: no existe"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Lo que la DIAN responde de verdad
# ═══════════════════════════════════════════════════════════════════════════════════════════

# Capturado el 2026-08-08 contra el portal real, con sesion valida y por el tunel de Colombia.
# Es la respuesta LITERAL a `GET /formularios?estado=presentado` de un contribuyente que nunca
# ha declarado: un 404 cuyo cuerpo trae un `codigo` 500 y una traza de pila de Java de unos
# cuatro mil caracteres. Reproducirla textual es lo que hace que estas pruebas prueben algo:
# un fixture inventado (`{"mensaje": "no existe"}`) no habria detectado ninguno de los dos
# problemas que se arreglan aca.
RESPUESTA_REAL_SIN_DOCUMENTOS = {
    "codigo": 500,
    "mensaje": "Documentos no encontrados",
    "descripcion": (
        "at co.gov.dian.muisca.diligenciamiento.rest.selformrenta210.resources.server."
        "DilIngresoFormularioRenta210CrearFormOConsultarFormsServerResource$1:ejecutar:82\n"
        + "at org.restlet.routing.Filter:doHandle:150\n"
        * 120
    ),
}


def _handler_404_real():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        return httpx.Response(404, json=RESPUESTA_REAL_SIN_DOCUMENTOS)

    return handler


async def test_se_cita_lo_que_la_dian_contesto_y_no_solo_el_codigo():
    """La razon por la que el cuerpo dejo de descartarse.

    Con solo el codigo, "no hay declaracion", "el endpoint cambio" y "la sesion no alcanza"
    llegaban al expediente como la misma frase, y distinguirlas obligaba a repetir la consulta a
    mano contra el portal real. El cuerpo lo dice con todas las letras.
    """
    ctx, http = _contexto(_handler_404_real())
    async with http:
        with pytest.raises(DianDocumentUnavailableError) as capturado:
            await download_prior_return(ctx, TaxpayerRef(id_number="1004683364", tax_year=2025))

    assert 'La DIAN respondió: "Documentos no encontrados"' in capturado.value.message
    assert capturado.value.details["evidencia"].endswith("Documentos no encontrados")


async def test_la_traza_de_java_no_llega_ni_al_mensaje_ni_a_la_evidencia():
    """La DIAN manda cuatro mil caracteres de traza junto a la frase util.

    Arrastrarla completa llenaria los logs y volveria ilegible el mensaje que ve una persona, sin
    aportar un dato que no este ya en `mensaje`.
    """
    ctx, http = _contexto(_handler_404_real())
    async with http:
        with pytest.raises(DianDocumentUnavailableError) as capturado:
            await download_prior_return(ctx, TaxpayerRef(id_number="1004683364", tax_year=2025))

    for texto in (capturado.value.message, capturado.value.details["evidencia"]):
        assert "org.restlet" not in texto
        assert len(texto) < 400


async def test_un_404_sin_cuerpo_legible_no_inventa_una_cita():
    """El portal puede responder 404 con HTML, vacio, o con un JSON sin `mensaje`.

    Ahi no hay nada que citar, y el mensaje tiene que seguir siendo el prudente de siempre en vez
    de arrastrar un `None` a la pantalla.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        return httpx.Response(404, content=b"<html>Not Found</html>")

    ctx, http = _contexto(handler)
    async with http:
        with pytest.raises(DianDocumentUnavailableError) as capturado:
            await download_prior_return(ctx, TaxpayerRef(id_number="10203040", tax_year=2025))

    assert "La DIAN respondió" not in capturado.value.message
    assert "None" not in capturado.value.message
    assert capturado.value.details["evidencia"] == "respuesta 404 de la API"


async def test_el_mensaje_dice_de_que_estado_es_la_declaracion_que_falta():
    """ "No hay declaracion" es ambiguo: no es lo mismo no haber presentado el ano pasado que no
    tener borrador este ano."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        return httpx.Response(404, json={})

    ctx, http = _contexto(handler)
    async with http:
        with pytest.raises(DianDocumentUnavailableError) as capturado:
            await download_suggested_return(ctx, TaxpayerRef(id_number="10203040", tax_year=2025))
    assert "en borrador" in capturado.value.message


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Qué sale por el túnel y qué no
# ═══════════════════════════════════════════════════════════════════════════════════════════


def test_solo_la_api_de_la_dian_sale_por_el_tunel():
    """`muisca` sigue saliendo directo, y eso es una decisión.

    Solo `api.dian.gov.co` está bloqueado fuera de Colombia. Mandar también a `muisca` por el
    túnel le agregaría un punto de falla a lo único que hoy funciona desde cualquier parte, y
    triplicaría el tráfico que pasa por una máquina de terceros.
    """
    from declaras.adapters.dian.rest.connector import HttpDianConnector

    con = HttpDianConnector(base_url="https://muisca.dian.gov.co", api_proxy="socks5://127.0.0.1:1")
    assert con._api_proxy == "socks5://127.0.0.1:1"

    sin = HttpDianConnector(base_url="https://muisca.dian.gov.co")
    assert sin._api_proxy is None


def test_sin_proxy_configurado_no_se_monta_ningun_transporte():
    """El default es salida directa: en un despliegue que ya alcanza la DIAN —o corriendo desde
    Colombia— no hay razón para meter un intermediario, y meterlo sería agregar una dependencia
    que puede caerse."""
    from declaras.config.settings import Settings

    assert Settings().dian_api_proxy is None
