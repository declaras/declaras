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
