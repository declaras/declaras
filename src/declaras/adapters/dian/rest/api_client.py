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
    DianDocumentUnavailableError,
    DianError,
    DianPortalUnavailableError,
    DianRateLimitedError,
    DianSessionExpiredError,
    DianTimeoutError,
)
from declaras.observability import get_logger

log = get_logger(__name__)

_SESSION_COOKIE = "DIAN-MUISCA"


def motivo_de_la_dian(response: httpx.Response) -> str | None:
    """El `mensaje` con que la DIAN explica un error, si viene y es presentable.

    ═══ POR QUE HAY QUE LEER EL CUERPO Y NO SOLO EL CODIGO ═══

    Un 404 de esta API es ambiguo hasta la inutilidad: puede ser "no hay documentos", un
    endpoint que cambio, o un recurso al que esta sesion no alcanza. Se descartaba el cuerpo y se
    conservaba solo el numero, asi que las tres cosas llegaban al expediente como una sola frase,
    y no habia forma de saber cual era sin volver a correr la consulta a mano contra el portal
    real. Se hizo, y el cuerpo lo decia con todas las letras: `Documentos no encontrados`.

    ═══ POR QUE SOLO `mensaje` Y NO EL CUERPO ENTERO ═══

    La DIAN acompana ese texto con un `descripcion` que es una traza de pila de Java de unos
    cuatro mil caracteres. Guardarla completa llenaria los logs y el mensaje que ve una persona
    sin agregar un dato que no este ya en `mensaje`. Se toma la frase y se descarta el resto; el
    tope de 200 caracteres es por si algun dia esa frase tampoco es una frase.
    """
    if "json" not in response.headers.get("content-type", ""):
        return None
    try:
        cuerpo = response.json()
    except ValueError:
        return None
    mensaje = cuerpo.get("mensaje") if isinstance(cuerpo, dict) else None
    if not isinstance(mensaje, str) or not mensaje.strip():
        return None
    return mensaje.strip()[:200]


def _raise_for_status(response: httpx.Response, *, url: str) -> None:
    """Traduce la respuesta de la API a un error del dominio.

    Sin esto, cualquier codigo distinto de 2xx sale como una excepcion de la libreria HTTP, que
    nadie reconoce como una falla de la DIAN y termina reportada como error interno. Paso con un
    contribuyente que nunca habia declarado: la API responde 404 al preguntar por su declaracion
    presentada, la extraccion entera se cayo con "INTERNAL_ERROR" y el texto crudo de httpx, y
    quien la lanzo no supo ni si la clave estaba bien.

    Un 404 aqui casi nunca es un error: es la DIAN diciendo que no tiene ese documento.
    """
    codigo = response.status_code
    if codigo < 400:
        return
    if codigo == httpx.codes.UNAUTHORIZED:
        raise DianSessionExpiredError("La sesión con la DIAN se venció.")
    if codigo == httpx.codes.NOT_FOUND:
        raise DianDocumentUnavailableError(
            "La DIAN no tiene ese documento.",
            url=url,
            status=codigo,
            motivo=motivo_de_la_dian(response),
        )
    if codigo == httpx.codes.TOO_MANY_REQUESTS:
        raise DianRateLimitedError("La DIAN está limitando las consultas.")
    if codigo >= 500:
        raise DianPortalUnavailableError("La DIAN respondió con un error.", url=url, status=codigo)
    raise DianError(f"La DIAN rechazó la consulta ({codigo}).", url=url, status=codigo)


def build_digest(cookie_value: str) -> str:
    """Construye el valor del header Digest a partir de la cookie de sesion."""
    return base64.b64encode(cookie_value.encode()).decode()


class DianApiClient:
    """Habla con api.dian.gov.co reusando la sesion del portal."""

    def __init__(
        self, client: httpx.AsyncClient, *, portal_url: str, por_tunel: bool = False
    ) -> None:
        self._client = client
        self._portal_url = portal_url.rstrip("/")
        self._bearer: str | None = None
        self._por_tunel = por_tunel

    @property
    def _falla_de_conexion(self) -> str:
        """El mensaje de un fallo de red, que cambia segun por donde iba la peticion.

        POR QUE NO ES UN SOLO TEXTO. Cuando hay tunel configurado, un fallo de conexion tiene dos
        causas posibles y muy distintas: se cayo la DIAN, o se cayo NUESTRO tunel. Con un mensaje
        unico —"la DIAN no responde"— quien opere va a revisar el portal de la DIAN, verlo
        funcionando, y perder un rato largo antes de sospechar de una maquina propia que nadie
        menciono. El texto tiene que nombrar al sospechoso que solo nosotros conocemos.
        """
        if self._por_tunel:
            return (
                "No se pudo conectar con la API de la DIAN a través del túnel configurado. "
                "Puede estar caída la DIAN o el túnel."
            )
        return "No se pudo establecer la conexión con la API de la DIAN."

    @property
    def is_authenticated(self) -> bool:
        return self._bearer is not None

    async def authenticate(self) -> None:
        """Canjea la cookie de sesion del portal por un token de la API."""
        cookie = self._client.cookies.get(_SESSION_COOKIE)
        if not cookie:
            raise DianSessionExpiredError("No hay sesión abierta en el portal de la DIAN.")
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
            raise DianTimeoutError("La DIAN no respondió a tiempo.") from exc
        except httpx.TransportError as exc:
            # NO SOLO EL TIMEOUT. Atrapar unicamente `TimeoutException` dejaba escapar
            # `ConnectError` —el DNS que no resuelve, el TCP rechazado, el handshake TLS que el
            # servidor corta— y esa excepcion NO es un `DianError`, asi que se saltaba el manejo
            # por documento de `extraction._collect` (que ya sabe seguir con los demas) y moria en
            # el `except Exception` general, matando la consulta entera.
            #
            # Medido en produccion: `api.dian.gov.co` corta el handshake desde Railway. La consulta
            # bajaba RUT y exogena, y al llegar al cuarto documento tumbaba las tres descargas que
            # ya estaban hechas. El trabajo quedaba FAILED con will_retry=false.
            #
            # `TransportError` es el padre de las dos, asi que este par cubre la familia completa.
            raise DianPortalUnavailableError(
                self._falla_de_conexion,
                url=f"{DIAN_API.base_url}{DIAN_API.token_from_cookies}",
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise DianSessionExpiredError(
                "La DIAN rechazó el canje de la sesión.", status_code=response.status_code
            )

        payload = response.json()
        self._bearer = payload.get("idToken")
        if not self._bearer:
            raise DianPortalUnavailableError("La DIAN no entregó el token de acceso.")
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
        except httpx.TransportError as exc:
            # Ver la nota de `authenticate`: sin esta rama un `ConnectError` no era un `DianError`
            # y tumbaba la consulta completa en vez de solo este documento.
            raise DianPortalUnavailableError(self._falla_de_conexion, url=url) from exc

        _raise_for_status(response, url=url)
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
        except httpx.TransportError as exc:
            # Ver la nota de `authenticate`: sin esta rama un `ConnectError` no era un `DianError`
            # y tumbaba la consulta completa en vez de solo este documento.
            raise DianPortalUnavailableError(self._falla_de_conexion, url=url) from exc

        _raise_for_status(response, url=url)
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
