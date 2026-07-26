"""Ciclo de vida de Playwright y traduccion de fallas tecnicas a errores del dominio.

El Muisca es fragil, asi que aca viven tres protecciones: limite de sesiones
concurrentes, timeouts explicitos y deteccion de paginas de mantenimiento.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import TracebackType

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from declaras.adapters.dian.selectors import SELECTORS
from declaras.domain.errors import (
    DianPortalUnavailableError,
    DianSessionExpiredError,
    DianTimeoutError,
)
from declaras.observability import get_logger

log = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserPool:
    """Un solo navegador, muchos contextos aislados, con tope de concurrencia.

    Cada contribuyente corre en su propio BrowserContext: cookies y sesion nunca se
    mezclan entre personas.
    """

    def __init__(self, *, headless: bool, max_concurrent: int, nav_timeout_ms: int) -> None:
        self._headless = headless
        self._nav_timeout_ms = nav_timeout_ms
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            log.info("browser.started", headless=self._headless)

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is not None:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                with suppress(Exception):
                    await self._playwright.stop()
                self._playwright = None
            log.info("browser.stopped")

    async def acquire_context(self) -> ManagedContext:
        """Reserva un cupo de concurrencia y devuelve un contexto aislado."""
        await self.start()
        await self._semaphore.acquire()
        try:
            assert self._browser is not None
            context = await self._browser.new_context(
                user_agent=_USER_AGENT,
                accept_downloads=True,
                locale="es-CO",
                timezone_id="America/Bogota",
                viewport={"width": 1440, "height": 900},
            )
            context.set_default_timeout(self._nav_timeout_ms)
            context.set_default_navigation_timeout(self._nav_timeout_ms)
            return ManagedContext(context, release=self._semaphore.release)
        except Exception:
            self._semaphore.release()
            raise


class ManagedContext:
    """Contexto de navegador que libera el cupo de concurrencia al cerrarse."""

    def __init__(self, context: BrowserContext, *, release: object) -> None:
        self._context = context
        self._release = release
        self._closed = False

    @property
    def context(self) -> BrowserContext:
        return self._context

    async def new_page(self) -> Page:
        return await self._context.new_page()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(Exception):
            await self._context.close()
        if callable(self._release):
            self._release()

    async def __aenter__(self) -> ManagedContext:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


async def goto(page: Page, url: str) -> None:
    """Navega y traduce fallas de red o mantenimiento a errores del dominio."""
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except PlaywrightTimeout as exc:
        raise DianTimeoutError(url=url) from exc
    except PlaywrightError as exc:
        raise DianPortalUnavailableError(url=url, reason=str(exc)[:200]) from exc
    await assert_portal_healthy(page)


async def assert_portal_healthy(page: Page) -> None:
    """Detecta mantenimiento o sesion caida antes de seguir interactuando."""
    if await _is_visible(page, SELECTORS.maintenance_marker):
        raise DianPortalUnavailableError("el portal reporta mantenimiento")
    if await _is_visible(page, SELECTORS.session_expired_marker):
        raise DianSessionExpiredError()


async def _is_visible(page: Page, selector: str, *, timeout_ms: int = 800) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout_ms)
    except (PlaywrightTimeout, PlaywrightError):
        return False


async def is_visible(page: Page, selector: str, *, timeout_ms: int = 1_500) -> bool:
    """Version publica: presencia de un marcador sin lanzar excepcion."""
    return await _is_visible(page, selector, timeout_ms=timeout_ms)
