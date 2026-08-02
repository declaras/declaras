"""Conector HTTP: autentica y entrega sesiones sin usar navegador.

Es el camino preferido. Frente a Playwright: no requiere Chromium, responde en segundos
en vez de decenas de segundos, consume una fraccion de memoria y es trivial de correr en
un contenedor pequeno. El navegador queda para los flujos que dependan de JavaScript.
"""

from __future__ import annotations

import httpx

from declaras.adapters.dian.endpoints import DASHBOARD_FORM, ENDPOINTS, USER_AGENT
from declaras.adapters.dian.rest.auth import authenticate
from declaras.adapters.dian.rest.session import HttpDianSession
from declaras.domain.errors import (
    DianInvalidCredentialsError,
    DianPortalUnavailableError,
    DianTimeoutError,
)
from declaras.domain.models import DianCredentials, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)


class HttpDianConnector:
    """Implementa DianConnector sobre httpx."""

    def __init__(self, *, base_url: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def open_session(
        self, credentials: DianCredentials, taxpayer: TaxpayerRef
    ) -> HttpDianSession:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-CO,es;q=0.9"},
        )
        try:
            await authenticate(client, base_url=self._base_url, credentials=credentials)
            await self._verify_session(client)
        except Exception:
            await client.aclose()
            raise

        log.info("dian.http.session_opened")
        return HttpDianSession(client=client, base_url=self._base_url)

    async def shutdown(self) -> None:
        return None

    async def _verify_session(self, client: httpx.AsyncClient) -> None:
        """Confirma que la sesion sirve de verdad pidiendo el dashboard.

        Es la forma fiable de distinguir una clave incorrecta: el portal puede responder
        200 al login y aun asi no dejar sesion utilizable.
        """
        url = f"{self._base_url}{ENDPOINTS.dashboard}"
        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise DianTimeoutError(url=url) from exc
        except httpx.TransportError as exc:
            # Mismo hueco que en `api_client`: un fallo de conexion tiene que llegar al dominio
            # como `DianError`, no como excepcion de httpx.
            raise DianPortalUnavailableError(
                "No se pudo establecer la conexión con el portal de la DIAN."
            ) from exc

        if DASHBOARD_FORM.authenticated_marker not in response.text:
            log.warning("dian.http.login_rejected", status_code=response.status_code)
            raise DianInvalidCredentialsError()
