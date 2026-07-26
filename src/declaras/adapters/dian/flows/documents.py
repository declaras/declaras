"""Descarga de los insumos que alimentan el borrador de la declaracion.

ARQUITECTURA (calibrada contra el portal real el 2026-07-25): el Muisca es JSF y sus
enlaces son href="#" con submit por JavaScript, asi que **no se puede navegar por URL**.
Cada flujo vuelve al dashboard y hace clic en el icono correspondiente, cuyos ids son
estables (ver DashboardSelectors).

Estado por documento:
  RUT                 CALIBRADO: un clic entrega el PDF como descarga directa.
  EXOGENA             PENDIENTE: el icono del dashboard no navega; falta hallar la ruta.
  PRIOR_RETURN        PENDIENTE: vive bajo "Consultar documento Diligenciado".
  SUGGESTED_RETURN    PENDIENTE.
  EINVOICE_SUMMARY    PENDIENTE: el icono no navega; el portal de FE es externo.

Los pendientes fallan con DIAN_LAYOUT_CHANGED, que es honesto: el job reporta falla
parcial por ese documento y entrega los demas.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from declaras.adapters.dian.browser import goto
from declaras.adapters.dian.flows.helpers import (
    assert_has_results,
    capture_download,
    click,
    fill,
    render_pdf,
    select_option,
)
from declaras.adapters.dian.selectors import SELECTORS
from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)

Downloader = Callable[[Page, str, TaxpayerRef], Awaitable[RawDocument]]

_FORM_210 = "210"


async def go_to_dashboard(page: Page, base_url: str) -> None:
    """Vuelve al dashboard, que es el unico punto de partida confiable de navegacion."""
    await goto(page, f"{base_url.rstrip('/')}{SELECTORS.dashboard.path}")
    await page.wait_for_timeout(2_000)  # el JSF tarda en pintar los iconos


async def download_rut(page: Page, base_url: str, taxpayer: TaxpayerRef) -> RawDocument:
    """RUT actualizado.

    CALIBRADO: el icono "Obtener copia RUT" del dashboard entrega el PDF directamente
    como descarga, sin pasar por ninguna pagina intermedia.
    """
    await go_to_dashboard(page, base_url)
    filename, content = await capture_download(
        page, lambda: page.locator(SELECTORS.dashboard.rut_copy).first.click()
    )
    log.info("dian.rut.downloaded", filename=filename, size_bytes=len(content))
    return RawDocument(
        doc_type=DocumentType.RUT,
        filename=filename or f"rut-{taxpayer.id_number}.pdf",
        content=content,
        content_type="application/pdf",
        source_url=page.url,
        metadata={"via": "dashboard.rut_copy"},
    )


async def download_exogena(page: Page, base_url: str, taxpayer: TaxpayerRef) -> RawDocument:
    """Informacion exogena: lo que terceros le reportaron a la DIAN."""
    sel = SELECTORS.exogena
    await goto(page, f"{base_url.rstrip('/')}{sel.path}")
    await select_option(page, sel.year_select, str(taxpayer.tax_year), name="exogena.year_select")
    await click(page, sel.submit, name="exogena.submit")
    await assert_has_results(page, sel.empty_marker, doc_label="informacion exogena")

    filename, content = await capture_download(
        page, lambda: page.locator(sel.download_link).first.click()
    )
    return RawDocument(
        doc_type=DocumentType.EXOGENA,
        filename=filename or f"exogena-{taxpayer.tax_year}.zip",
        content=content,
        content_type="application/octet-stream",
        source_url=page.url,
        metadata={"tax_year": taxpayer.tax_year},
    )


async def download_prior_return(page: Page, base_url: str, taxpayer: TaxpayerRef) -> RawDocument:
    """Declaracion presentada del anio anterior: patrimonio inicial, anticipo, saldos."""
    sel = SELECTORS.returns
    prior_year = taxpayer.tax_year - 1
    await goto(page, f"{base_url.rstrip('/')}{sel.path}")
    await select_option(page, sel.year_select, str(prior_year), name="returns.year_select")
    await select_option(page, sel.form_select, _FORM_210, name="returns.form_select")
    await click(page, sel.submit, name="returns.submit")
    await assert_has_results(page, sel.empty_marker, doc_label=f"declaracion {prior_year}")

    filename, content = await capture_download(
        page, lambda: page.locator(sel.pdf_link).first.click()
    )
    return RawDocument(
        doc_type=DocumentType.PRIOR_RETURN,
        filename=filename or f"declaracion-{prior_year}.pdf",
        content=content,
        content_type="application/pdf",
        source_url=page.url,
        metadata={"tax_year": prior_year, "form": _FORM_210},
    )


async def download_suggested_return(
    page: Page, base_url: str, taxpayer: TaxpayerRef
) -> RawDocument:
    """Declaracion sugerida por la DIAN, cuando existe. Es contraste, nunca copia ciega."""
    sel = SELECTORS.returns
    await goto(page, f"{base_url.rstrip('/')}{sel.suggested_path}")
    await assert_has_results(page, sel.empty_marker, doc_label="declaracion sugerida")
    content = await render_pdf(page)
    return RawDocument(
        doc_type=DocumentType.SUGGESTED_RETURN,
        filename=f"declaracion-sugerida-{taxpayer.tax_year}.pdf",
        content=content,
        content_type="application/pdf",
        source_url=page.url,
        metadata={"tax_year": taxpayer.tax_year},
    )


async def download_einvoice_summary(
    page: Page, base_url: str, taxpayer: TaxpayerRef
) -> RawDocument:
    """Facturas electronicas recibidas: insumo directo de la deduccion del 1%."""
    sel = SELECTORS.einvoices
    await goto(page, f"{base_url.rstrip('/')}{sel.path}")
    await fill(page, sel.date_from, f"01/01/{taxpayer.tax_year}", name="einvoices.date_from")
    await fill(page, sel.date_to, f"31/12/{taxpayer.tax_year}", name="einvoices.date_to")
    await click(page, sel.submit, name="einvoices.submit")
    await assert_has_results(page, sel.empty_marker, doc_label="facturas electronicas")

    filename, content = await capture_download(
        page, lambda: page.locator(sel.export_button).first.click()
    )
    return RawDocument(
        doc_type=DocumentType.EINVOICE_SUMMARY,
        filename=filename or f"facturas-{taxpayer.tax_year}.xlsx",
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source_url=page.url,
        metadata={"tax_year": taxpayer.tax_year},
    )


DOWNLOADERS: dict[DocumentType, Downloader] = {
    DocumentType.RUT: download_rut,
    DocumentType.EXOGENA: download_exogena,
    DocumentType.PRIOR_RETURN: download_prior_return,
    DocumentType.SUGGESTED_RETURN: download_suggested_return,
    DocumentType.EINVOICE_SUMMARY: download_einvoice_summary,
}
