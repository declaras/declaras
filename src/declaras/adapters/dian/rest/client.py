"""Cliente del portal: concentra el trato con HTTP para que los flujos no lo repitan.

Los flujos de cada documento solo necesitan pedir el dashboard y enviar formularios; los
headers, la traduccion de timeouts y la validacion de sesion viven aqui.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from declaras.adapters.dian.endpoints import DASHBOARD_FORM, ENDPOINTS
from declaras.adapters.dian.rest.api_client import DianApiClient
from declaras.domain.errors import DianSessionExpiredError, DianTimeoutError


class PortalClient:
    """Envuelve un cliente HTTP ya autenticado contra el Muisca."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def dashboard_url(self) -> str:
        return f"{self._base_url}{ENDPOINTS.dashboard}"

    async def fetch_dashboard(self) -> str:
        """Devuelve el HTML del dashboard y valida que la sesion siga viva."""
        response = await self.get(self.dashboard_url)
        html = response.text
        if DASHBOARD_FORM.authenticated_marker not in html:
            raise DianSessionExpiredError("La sesión en el portal de la DIAN ya no es válida.")
        return html

    async def get(self, url: str) -> httpx.Response:
        try:
            return await self._client.get(url)
        except httpx.TimeoutException as exc:
            raise DianTimeoutError(url=url) from exc

    async def submit_form(
        self, payload: dict[str, str], *, url: str | None = None
    ) -> httpx.Response:
        """Envia un formulario al portal con los headers que el Muisca exige."""
        target = url or self.dashboard_url
        try:
            return await self._client.post(
                target,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": self._base_url,
                    "Referer": self.dashboard_url,
                },
            )
        except httpx.TimeoutException as exc:
            raise DianTimeoutError(url=target) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class PortalContext:
    """Lo que un flujo necesita para traer un documento.

    Algunos documentos salen del portal JSF y otros de la API REST, asi que los flujos
    reciben ambos accesos y cada uno usa el que le corresponde.
    """

    portal: PortalClient
    api: DianApiClient
