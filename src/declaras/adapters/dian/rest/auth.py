"""Autenticacion HTTP contra el Muisca, sin navegador.

Flujo real del portal, calibrado el 2026-07-25:

  1. GET a la entrada clasica: redirige a la app Angular y deja el `ideRequest` en la
     URL. Ese valor es un JSON en base64 con el clientId y el redirect_uri.
  2. POST a `weblogin` con las credenciales (la clave va en base64) mas los headers
     Origin y Referer apuntando a la app: sin ellos el servicio responde 500.
  3. La respuesta siembra las cookies de sesion legacy (JSESSIONID y DIAN-MUISCA), que
     son las que entiende el portal JSF.
"""

from __future__ import annotations

import base64
import binascii
import json
from urllib.parse import parse_qs, urlparse

import httpx

from declaras.adapters.dian.endpoints import ENDPOINTS, PORTAL_ID_CODES, SCOPE_OWN_BEHALF
from declaras.domain.errors import (
    DianInvalidCredentialsError,
    DianLayoutChangedError,
    DianPortalUnavailableError,
    DianTimeoutError,
)
from declaras.domain.models import DianCredentials
from declaras.observability import get_logger

log = get_logger(__name__)


async def authenticate(
    client: httpx.AsyncClient, *, base_url: str, credentials: DianCredentials
) -> None:
    """Deja el cliente con una sesion valida en el portal.

    Lanza DianInvalidCredentialsError si el portal no entrega sesion utilizable.
    """
    ide_request, client_id, redirect_uri = await _begin_session(client, base_url=base_url)
    await _submit_credentials(
        client,
        base_url=base_url,
        credentials=credentials,
        ide_request=ide_request,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )


async def _begin_session(client: httpx.AsyncClient, *, base_url: str) -> tuple[str, str, str]:
    """Obtiene el ideRequest que el portal exige para aceptar el login."""
    url = f"{base_url.rstrip('/')}{ENDPOINTS.login_entry}"
    try:
        response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise DianTimeoutError(url=url) from exc
    except httpx.HTTPError as exc:
        raise DianPortalUnavailableError(url=url, reason=str(exc)[:160]) from exc

    query = parse_qs(urlparse(str(response.url)).query)
    candidates = query.get("ideRequest") or []
    ide_request = candidates[0] if candidates else ""
    if not ide_request:
        raise DianLayoutChangedError(
            "la entrada del portal no entrego ideRequest",
            selector="login_entry.ideRequest",
            final_url=str(response.url)[:160],
        )

    try:
        padded = ide_request + "=" * (-len(ide_request) % 4)
        decoded = json.loads(base64.b64decode(padded))
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise DianLayoutChangedError("el ideRequest no es un JSON en base64") from exc

    client_id = decoded.get("clientId")
    redirect_uri = decoded.get("redirect_uri") or decoded.get("redirectUri")
    if not client_id or not redirect_uri:
        raise DianLayoutChangedError(
            "el ideRequest no trae clientId o redirect_uri", keys=list(decoded)[:8]
        )
    return ide_request, client_id, redirect_uri


async def _submit_credentials(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    credentials: DianCredentials,
    ide_request: str,
    client_id: str,
    redirect_uri: str,
) -> None:
    root = base_url.rstrip("/")
    id_code = PORTAL_ID_CODES.get(credentials.id_kind.value)
    if id_code is None:
        raise DianLayoutChangedError(
            f"tipo de documento sin equivalente en el portal: {credentials.id_kind.value}"
        )

    payload = {
        "aNombreDe": SCOPE_OWN_BEHALF,
        "numDocumentoOrg": credentials.on_behalf_of_nit or "null",
        "tipoDoc": id_code,
        "numDoc": credentials.id_number,
        # El portal espera la clave codificada en base64.
        "password": base64.b64encode(credentials.password.get_secret_value().encode()).decode(),
        "clientId": client_id,
        "redirectUri": redirect_uri,
        "ideRequest": ide_request,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": root,
        "Referer": f"{root}{ENDPOINTS.login_app_path}?ideRequest={ide_request}",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        response = await client.post(f"{root}{ENDPOINTS.weblogin}", data=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise DianTimeoutError("el portal no respondio al enviar las credenciales") from exc
    except httpx.HTTPError as exc:
        raise DianPortalUnavailableError(reason=str(exc)[:160]) from exc

    if response.status_code >= 500:
        # El portal responde 500 cuando el envio no cumple su contrato (por ejemplo,
        # sin Origin/Referer). Es falla nuestra o del portal, no del usuario.
        raise DianLayoutChangedError(
            "el servicio de login rechazo la peticion",
            status_code=response.status_code,
        )

    log.info("dian.http.login_submitted", status_code=response.status_code)
    if not _session_cookies_present(client):
        raise DianInvalidCredentialsError()


def _session_cookies_present(client: httpx.AsyncClient) -> bool:
    names = set(client.cookies.keys())
    return "DIAN-MUISCA" in names and "JSESSIONID" in names
