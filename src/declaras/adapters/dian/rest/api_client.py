"""Cliente de la API REST de la DIAN (api.dian.gov.co).

Detras del portal JSF existe una API moderna que usan las aplicaciones Angular de la
entidad. No sustituye al portal (los documentos historicos siguen saliendo por JSF), pero
si expone informacion que por JSF costaria varias navegaciones, y es el camino natural
para el presentador del formulario 210.

Como se autentica, calibrado el 2026-07-25:

  1. Se hace login normal en el portal, que deja la cookie `DIAN-MUISCA`.
  2. Se canjea esa cookie por un token: POST /identidad/sts/v2/cookies/token con el
     header `Authorization: Digest <base64 del valor de la cookie>`.
  3. Las llamadas siguientes van con `Authorization: Bearer <idToken>` y, esto es lo que
     no es obvio, el header `clientid`: sin el la API responde 400 o 401.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import httpx

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.domain.errors import (
    DianPortalUnavailableError,
    DianSessionExpiredError,
    DianTimeoutError,
)
from declaras.observability import get_logger

log = get_logger(__name__)

_SESSION_COOKIE = "DIAN-MUISCA"


def build_digest(cookie_value: str) -> str:
    """Construye el valor del header Digest a partir de la cookie de sesion."""
    return base64.b64encode(cookie_value.encode()).decode()


class DianApiClient:
    """Habla con api.dian.gov.co reusando la sesion del portal."""

    def __init__(self, client: httpx.AsyncClient, *, portal_url: str) -> None:
        self._client = client
        self._portal_url = portal_url.rstrip("/")
        self._bearer: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self._bearer is not None

    async def authenticate(self) -> None:
        """Canjea la cookie de sesion del portal por un token de la API."""
        cookie = self._client.cookies.get(_SESSION_COOKIE)
        if not cookie:
            raise DianSessionExpiredError("no hay sesion en el portal: falta la cookie de Muisca")
        try:
            response = await self._client.post(
                f"{DIAN_API.base_url}{DIAN_API.token_from_cookies}",
                headers={
                    "Authorization": f"Digest {build_digest(cookie)}",
                    "Referer": f"{self._portal_url}/",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise DianTimeoutError("la API de la DIAN no respondio") from exc

        if response.status_code != httpx.codes.OK:
            raise DianSessionExpiredError(
                "la API rechazo el canje de la sesion", status_code=response.status_code
            )

        payload = response.json()
        self._bearer = payload.get("idToken")
        if not self._bearer:
            raise DianPortalUnavailableError("la API no entrego idToken")
        log.info("dian.api.authenticated", expires_in=payload.get("expireIn"))

    async def get_json(self, path: str) -> Any:
        """Consulta un recurso de la API y devuelve el JSON."""
        if self._bearer is None:
            await self.authenticate()
        url = f"{DIAN_API.base_url}{path}"
        try:
            response = await self._client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise DianTimeoutError(url=url) from exc

        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise DianSessionExpiredError("el token de la API ya no es valido")
        response.raise_for_status()
        return response.json()

    async def get_bytes(self, path: str) -> tuple[bytes, httpx.Headers]:
        """Descarga un recurso binario de la API, como el PDF de una declaracion."""
        if self._bearer is None:
            await self.authenticate()
        url = f"{DIAN_API.base_url}{path}"
        headers = self._headers()
        headers["Accept"] = "application/pdf, application/octet-stream, */*"
        try:
            response = await self._client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise DianTimeoutError(url=url) from exc

        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise DianSessionExpiredError("el token de la API ya no es valido")
        response.raise_for_status()
        return response.content, response.headers

    def _headers(self) -> dict[str, str]:
        # x-request-id y etag comparten el mismo identificador, como hace el portal.
        request_id = str(uuid.uuid4())
        return {
            "Authorization": f"Bearer {self._bearer}",
            # Sin clientid la API responde 400 o 401 aunque el token sea valido.
            "clientid": DIAN_API.client_id,
            "Accept": "application/json",
            "Content-Type": "application/json;charset=ISO-8859-1",
            "Accept-Language": "es-CO",
            "Referer": f"{self._portal_url}/",
            "x-request-id": request_id,
            "etag": request_id,
        }
