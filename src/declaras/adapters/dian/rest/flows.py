"""Flujos de descarga por HTTP, uno por documento.

Cada flujo compone las primitivas de `jsf` sobre un `PortalClient`. Estan separados de la
sesion para que agregar un documento sea escribir una funcion y registrarla, sin tocar
nada mas.

Estado de calibracion contra el portal real (2026-07-25):
  RUT                un solo envio, entrega PDF
  EXOGENA            modal de anio en tres envios, entrega XLSX
  EINVOICE_SUMMARY   modal de anio en tres envios, entrega XLSX
"""

from __future__ import annotations

import httpx

from declaras.adapters.dian.endpoints import (
    DASHBOARD_FORM,
    EINVOICE_MODAL,
    EXOGENA_MODAL,
    YearModal,
)
from declaras.adapters.dian.rest import jsf
from declaras.adapters.dian.rest.client import PortalClient
from declaras.domain.errors import DianDocumentUnavailableError, DianLayoutChangedError
from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)

_FORM = DASHBOARD_FORM.form_id
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BINARY_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")


async def download_rut(client: PortalClient, taxpayer: TaxpayerRef) -> RawDocument:
    """RUT actualizado: el icono del dashboard entrega el PDF en un solo envio."""
    html = await client.fetch_dashboard()
    response = await client.submit_form(
        jsf.build_postback(html, form_id=_FORM, button_id=DASHBOARD_FORM.rut_copy)
    )
    _assert_is_document(response, DocumentType.RUT)
    return _to_document(
        response,
        doc_type=DocumentType.RUT,
        fallback_name=f"rut-{taxpayer.id_number}.pdf",
        content_type="application/pdf",
        source_url=client.dashboard_url,
        metadata={"via": "postback", "button": DASHBOARD_FORM.rut_copy},
    )


async def download_from_year_modal(
    client: PortalClient,
    taxpayer: TaxpayerRef,
    *,
    modal: YearModal,
    doc_type: DocumentType,
) -> RawDocument:
    """Descarga un tramite que se pide eligiendo el anio en una ventana modal.

    El orden de los tres envios no es negociable: el portal exige registrar el anio en un
    envio propio, y si se manda junto con la descarga responde el dashboard sin archivo.
    """
    year = str(taxpayer.tax_year)
    if taxpayer.tax_year < modal.min_year:
        raise DianDocumentUnavailableError(
            f"el portal sirve {doc_type.value} de {year} por otra via, aun no implementada",
            doc_type=doc_type.value,
            tax_year=taxpayer.tax_year,
        )

    year_fields = {modal.year_select: year, modal.year_hidden: year}

    html = await client.fetch_dashboard()
    opened = await client.submit_form(
        jsf.build_postback(html, form_id=_FORM, button_id=modal.open_button)
    )
    registered = await client.submit_form({**jsf.hidden_fields(opened.text, _FORM), **year_fields})
    response = await client.submit_form(
        jsf.build_link_postback(
            registered.text, form_id=_FORM, link_id=modal.action_link, extra=year_fields
        )
    )

    _assert_is_document(response, doc_type, tax_year=taxpayer.tax_year)
    return _to_document(
        response,
        doc_type=doc_type,
        fallback_name=modal.fallback_filename.format(year=year),
        content_type=_XLSX_CONTENT_TYPE,
        source_url=client.dashboard_url,
        metadata={"via": "year_modal", "tax_year": taxpayer.tax_year},
    )


async def download_exogena(client: PortalClient, taxpayer: TaxpayerRef) -> RawDocument:
    """Informacion exogena del anio gravable: lo que terceros le reportaron a la DIAN."""
    return await download_from_year_modal(
        client, taxpayer, modal=EXOGENA_MODAL, doc_type=DocumentType.EXOGENA
    )


async def download_einvoice_summary(client: PortalClient, taxpayer: TaxpayerRef) -> RawDocument:
    """Facturas electronicas recibidas: insumo de la deduccion del 1%."""
    return await download_from_year_modal(
        client, taxpayer, modal=EINVOICE_MODAL, doc_type=DocumentType.EINVOICE_SUMMARY
    )


DOWNLOADERS = {
    DocumentType.RUT: download_rut,
    DocumentType.EXOGENA: download_exogena,
    DocumentType.EINVOICE_SUMMARY: download_einvoice_summary,
}


# ─────────────────────────── internos ───────────────────────────


def _assert_is_document(
    response: httpx.Response, doc_type: DocumentType, **details: object
) -> None:
    """Verifica que el portal devolvio un archivo y no una pagina.

    Distingue los dos casos que importan: el portal contesto que no hay informacion
    (reintentar no sirve, se sigue sin ese documento) o el portal cambio (hay que
    recalibrar).
    """
    if response.content[:4] in _BINARY_SIGNATURES:
        return

    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        raise DianDocumentUnavailableError(
            "el portal devolvio una pagina en vez del documento",
            doc_type=doc_type.value,
            **details,
        )
    raise DianLayoutChangedError(
        "la respuesta del portal no es un documento reconocible",
        doc_type=doc_type.value,
        content_type=content_type,
        **details,
    )


def _to_document(
    response: httpx.Response,
    *,
    doc_type: DocumentType,
    fallback_name: str,
    content_type: str,
    source_url: str,
    metadata: dict[str, object],
) -> RawDocument:
    filename = jsf.filename_from_disposition(
        response.headers.get("content-disposition"), fallback=fallback_name
    )
    log.info(
        "dian.http.downloaded",
        doc_type=doc_type.value,
        filename=filename,
        size_bytes=len(response.content),
    )
    return RawDocument(
        doc_type=doc_type,
        filename=filename,
        content=response.content,
        content_type=response.headers.get("content-type", content_type).split(";")[0],
        source_url=source_url,
        metadata=metadata,
    )
