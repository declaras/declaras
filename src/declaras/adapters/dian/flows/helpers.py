"""Utilidades compartidas por los flujos de descarga."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from declaras.adapters.dian.browser import assert_portal_healthy, is_visible
from declaras.domain.errors import (
    DianDocumentUnavailableError,
    DianLayoutChangedError,
    DianTimeoutError,
)
from declaras.observability import get_logger

log = get_logger(__name__)

Trigger = Callable[[], Awaitable[None]]


async def capture_download(
    page: Page, trigger: Trigger, *, timeout_ms: int = 60_000
) -> tuple[str, bytes]:
    """Ejecuta una accion que dispara descarga y devuelve (nombre, contenido)."""
    try:
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await trigger()
        download = await download_info.value
        path = await download.path()
        if path is None:  # pragma: no cover - depende del navegador
            raise DianDocumentUnavailableError("la descarga no produjo archivo")
        return download.suggested_filename, path.read_bytes()
    except PlaywrightTimeout as exc:
        raise DianTimeoutError("el portal no entrego la descarga a tiempo") from exc


async def render_pdf(page: Page) -> bytes:
    """Imprime la pagina actual a PDF. Sirve cuando el portal no ofrece descarga."""
    try:
        return await page.pdf(format="Letter", print_background=True)
    except PlaywrightError as exc:
        raise DianLayoutChangedError("no se pudo renderizar la pagina a PDF") from exc


async def click(page: Page, selector: str, *, name: str) -> None:
    """Click con traduccion de fallas: si no existe el elemento, el portal cambio."""
    try:
        await page.locator(selector).first.click()
        await page.wait_for_load_state("domcontentloaded")
    except PlaywrightTimeout as exc:
        raise DianLayoutChangedError(f"no se encontro {name}", selector=name) from exc
    await assert_portal_healthy(page)


async def select_option(page: Page, selector: str, value: str, *, name: str) -> None:
    try:
        await page.locator(selector).first.select_option(value)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(f"no se pudo seleccionar {name}", selector=name) from exc


async def fill(page: Page, selector: str, value: str, *, name: str) -> None:
    try:
        await page.locator(selector).first.fill(value)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise DianLayoutChangedError(f"no se pudo llenar {name}", selector=name) from exc


async def assert_has_results(page: Page, empty_marker: str, *, doc_label: str) -> None:
    """Distingue 'el portal fallo' de 'el documento aun no existe'.

    Es la diferencia entre reintentar y decirle al usuario que la DIAN todavia no
    publica su informacion.
    """
    if await is_visible(page, empty_marker):
        raise DianDocumentUnavailableError(
            f"el portal no tiene {doc_label} para el periodo consultado",
            doc_label=doc_label,
        )
