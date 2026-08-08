"""Flujos de descarga por HTTP, uno por documento.

Cada flujo compone las primitivas de `jsf` sobre un `PortalClient`. Estan separados de la
sesion para que agregar un documento sea escribir una funcion y registrarla, sin tocar
nada mas.

Estado de calibracion contra el portal real (2026-07-25):
  RUT                postback simple, entrega PDF
  EXOGENA            modal de anio en tres envios, entrega XLSX
  EINVOICE_SUMMARY   modal de anio en tres envios, entrega XLSX
  PRIOR_RETURN       API REST: se busca el id y se descarga el PDF
  SUGGESTED_RETURN   API REST: el borrador abierto del anio en curso
"""

from __future__ import annotations

import httpx

from declaras.adapters.dian.endpoints import (
    DASHBOARD_FORM,
    DIAN_API,
    EINVOICE_MODAL,
    EXOGENA_MODAL,
    YearModal,
)
from declaras.adapters.dian.rest import jsf
from declaras.adapters.dian.rest.client import PortalContext
from declaras.domain.errors import DianDocumentUnavailableError, DianLayoutChangedError
from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)

_FORM = DASHBOARD_FORM.form_id
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BINARY_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")


async def download_rut(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """RUT actualizado: el icono del dashboard entrega el PDF en un solo envio."""
    html = await ctx.portal.fetch_dashboard()
    response = await ctx.portal.submit_form(
        jsf.build_postback(html, form_id=_FORM, button_id=DASHBOARD_FORM.rut_copy)
    )
    _assert_is_document(response, DocumentType.RUT)
    return _to_document(
        response,
        doc_type=DocumentType.RUT,
        fallback_name=f"rut-{taxpayer.id_number}.pdf",
        content_type="application/pdf",
        source_url=ctx.portal.dashboard_url,
        metadata={"via": "postback", "button": DASHBOARD_FORM.rut_copy},
    )


async def download_from_year_modal(
    ctx: PortalContext,
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
            f"El portal de la DIAN entrega {doc_type.value} de {year} por otra vía, "
            "que todavía no está implementada.",
            doc_type=doc_type.value,
            tax_year=taxpayer.tax_year,
        )

    year_fields = {modal.year_select: year, modal.year_hidden: year}

    html = await ctx.portal.fetch_dashboard()
    opened = await ctx.portal.submit_form(
        jsf.build_postback(html, form_id=_FORM, button_id=modal.open_button)
    )
    registered = await ctx.portal.submit_form(
        {**jsf.hidden_fields(opened.text, _FORM), **year_fields}
    )
    response = await ctx.portal.submit_form(
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
        source_url=ctx.portal.dashboard_url,
        metadata={"via": "year_modal", "tax_year": taxpayer.tax_year},
    )


async def download_exogena(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """Informacion exogena del anio gravable: lo que terceros le reportaron a la DIAN."""
    return await download_from_year_modal(
        ctx, taxpayer, modal=EXOGENA_MODAL, doc_type=DocumentType.EXOGENA
    )


async def download_einvoice_summary(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """Facturas electronicas recibidas: insumo de la deduccion del 1%."""
    return await download_from_year_modal(
        ctx, taxpayer, modal=EINVOICE_MODAL, doc_type=DocumentType.EINVOICE_SUMMARY
    )


# Como se llama cada estado de una declaracion cuando hay que explicarselo a alguien.
_ESTADO_LEGIBLE = {DIAN_API.state_filed: "presentada", DIAN_API.state_pending: "en borrador"}


async def _find_declaration(
    ctx: PortalContext, *, year: int, state: str, doc_type: DocumentType
) -> str:
    """Busca en la API el id de la declaracion del anio y estado indicados.

    DOS SENALES CON MUY DISTINTA FUERZA, y el mensaje tiene que reflejarlo.

    Cuando la API DEVUELVE LA LISTA y ese ano no esta, sabemos bastante: la DIAN enumero lo que
    tiene y ese ano no aparece. Ahi se puede afirmar la ausencia, y ademas se dicen los anos que
    si tiene, que es lo que permite ver de un vistazo si la respuesta tiene sentido.

    Cuando la API responde 404, sabemos MUCHO MENOS. Se venia traduciendo a "la DIAN no tiene
    ninguna declaracion a nombre del contribuyente", que es una afirmacion categorica sobre la
    vida tributaria de una persona derivada de un codigo HTTP ambiguo: un 404 tambien es un
    endpoint que cambio, un parametro que dejo de servir, o un recurso al que esta sesion no
    alcanza. Suponerlo es comodo y puede ser falso.

    Y la diferencia no es de redaccion. Si el contribuyente SI presento declaracion y aquí se
    afirma que no, el patrimonio inicial y los arrastres entran vacios a la declaracion nueva sin
    que nadie lo note — que es exactamente la clase de error silencioso que este proyecto evita.
    Por eso el 404 ahora dice lo que de verdad se observo y pide confirmarlo.
    """
    como_se_llama = _ESTADO_LEGIBLE.get(state, state)
    try:
        payload = await ctx.api.get_json(f"{DIAN_API.renta_forms}?estado={state}")
    except DianDocumentUnavailableError as exc:
        raise DianDocumentUnavailableError(
            f"La DIAN no reportó ninguna declaración {como_se_llama}. Si el contribuyente sí "
            f"declaró el {year}, hay que verificarlo en el portal: puede que la consulta haya "
            "fallado y no que la declaración no exista.",
            doc_type=doc_type.value,
            tax_year=year,
            evidencia="respuesta 404 de la API",
        ) from exc

    listado = (payload or {}).get("listadoFormularios", {}).get("infoFormularios", [])
    anios = sorted({i.get("anio") for i in listado if i.get("anio")}, reverse=True)
    for item in listado:
        if item.get("anio") == year:
            return str(item["identificador"]["id"])
    tiene = f"; sí la tiene de {', '.join(str(a) for a in anios)}" if anios else ""
    raise DianDocumentUnavailableError(
        # Aca SI se afirma: la DIAN enumero lo que tiene y ese ano no esta.
        f"La DIAN no tiene la declaración {como_se_llama} del año gravable {year}{tiene}.",
        doc_type=doc_type.value,
        tax_year=year,
        available_years=anios,
        evidencia="listado de la DIAN, sin ese año",
    )


async def _download_declaration(
    ctx: PortalContext, *, form_id: str, doc_type: DocumentType, year: int, filename: str
) -> RawDocument:
    """Descarga el PDF de una declaracion por su identificador."""
    path = DIAN_API.renta_form_download.format(form_id=form_id)
    content, headers = await ctx.api.get_bytes(path)
    if not jsf.looks_like_pdf(content):
        raise DianLayoutChangedError(
            "La DIAN no devolvió un PDF de la declaración.",
            doc_type=doc_type.value,
            form_id=form_id,
        )
    log.info(
        "dian.api.declaration_downloaded",
        doc_type=doc_type.value,
        form_id=form_id,
        size_bytes=len(content),
    )
    return RawDocument(
        doc_type=doc_type,
        filename=jsf.filename_from_disposition(headers.get("content-disposition"), filename),
        content=content,
        content_type="application/pdf",
        source_url=f"{DIAN_API.base_url}{path}",
        metadata={"via": "api", "form_id": form_id, "tax_year": year},
    )


async def download_prior_return(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """Declaracion presentada del anio anterior.

    Aporta el patrimonio inicial, el anticipo pagado y los saldos a favor, que son los
    insumos de la comparacion patrimonial.
    """
    year = taxpayer.tax_year - 1
    form_id = await _find_declaration(
        ctx, year=year, state=DIAN_API.state_filed, doc_type=DocumentType.PRIOR_RETURN
    )
    return await _download_declaration(
        ctx,
        form_id=form_id,
        doc_type=DocumentType.PRIOR_RETURN,
        year=year,
        filename=f"declaracion-{year}.pdf",
    )


async def download_filed_return(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """La declaracion ya presentada DEL MISMO anio gravable del expediente.

    Es lo que se presento de verdad ese anio, que en la practica es lo que hizo un contador. Sirve
    para rehacer un anio viejo con el sistema y comparar casilla por casilla.

    Se diferencia de `download_prior_return` solo en el anio: aquella baja la del anio ANTERIOR
    porque la necesita como insumo (patrimonio inicial, anticipos, saldos a favor); esta baja la
    del anio que se esta rehaciendo, porque la necesita como CONTRASTE.

    En el anio en curso no existe todavia y se reporta como no disponible, igual que el resto: la
    extraccion sigue sin ella.
    """
    form_id = await _find_declaration(
        ctx,
        year=taxpayer.tax_year,
        state=DIAN_API.state_filed,
        doc_type=DocumentType.FILED_RETURN,
    )
    return await _download_declaration(
        ctx,
        form_id=form_id,
        doc_type=DocumentType.FILED_RETURN,
        year=taxpayer.tax_year,
        filename=f"declaracion-presentada-{taxpayer.tax_year}.pdf",
    )


async def download_suggested_return(ctx: PortalContext, taxpayer: TaxpayerRef) -> RawDocument:
    """Borrador abierto del anio gravable en curso.

    La DIAN precrea un borrador con la informacion que ya conoce, asi que sirve de
    contraste contra el borrador propio. Si el contribuyente no tiene ninguno abierto, se
    reporta como documento no disponible y la extraccion continua sin el.
    """
    form_id = await _find_declaration(
        ctx,
        year=taxpayer.tax_year,
        state=DIAN_API.state_pending,
        doc_type=DocumentType.SUGGESTED_RETURN,
    )
    return await _download_declaration(
        ctx,
        form_id=form_id,
        doc_type=DocumentType.SUGGESTED_RETURN,
        year=taxpayer.tax_year,
        filename=f"borrador-{taxpayer.tax_year}.pdf",
    )


DOWNLOADERS = {
    DocumentType.RUT: download_rut,
    DocumentType.EXOGENA: download_exogena,
    DocumentType.EINVOICE_SUMMARY: download_einvoice_summary,
    DocumentType.PRIOR_RETURN: download_prior_return,
    DocumentType.SUGGESTED_RETURN: download_suggested_return,
    DocumentType.FILED_RETURN: download_filed_return,
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
            "El portal de la DIAN devolvió una página en vez del documento.",
            doc_type=doc_type.value,
            **details,
        )
    raise DianLayoutChangedError(
        "La respuesta del portal de la DIAN no es un documento reconocible.",
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
