"""La clave del portal del cliente: guardarla, usarla y olvidarla.

═══ POR QUE SE GUARDA, SI ANTES SE DESTRUIA ═══

Preparar una declaracion son varias visitas al portal repartidas en dias: consultar, volver a
consultar cuando la DIAN publica la exogena, escribir el borrador. Con la clave efimera cada
una la pedia de nuevo, y quien opera la consola NO la tiene: hay que llamar al cliente. En la
practica, una llamada por paso.

═══ LO QUE CAMBIA CON ESO, Y NO ES SOLO TECNICO ═══

Mientras la clave se destruia, la pantalla podia decir "no queda guardada en ninguna parte". Al
guardarla ese texto seria mentira, asi que cambia tambien: se dice que queda guardada, cifrada,
y que se puede borrar cuando quiera. Guardar una credencial ajena y seguir diciendo lo
contrario no es un detalle de copy, es la diferencia entre pedir permiso y no pedirlo.

Por eso el borrado no es una funcion opcional de este servicio: una clave guardada sin forma de
retirarla no es una funcion, es una trampa.

═══ COMO SE GUARDA ═══

Cifrada con la llave del despliegue (`DECLARAS_CLAVE_DE_CIFRADO`), la misma que protege las del
embudo de consultas. Quien se lleve un dump de la base no puede leer ninguna. Y si la llave no
esta configurada, esto NO guarda en claro: no guarda.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from declaras.observability import get_logger
from declaras.services.cifrado import CifradoNoConfiguradoError, cifrar, descifrar

log = get_logger(__name__)


class ClaveService:
    def __init__(self, *, clients: object, llave: str | None) -> None:
        self._clients = clients
        self._llave = llave

    @property
    def disponible(self) -> bool:
        """Si este despliegue puede guardar claves. Sin llave de cifrado, no."""
        return bool(self._llave)

    async def guardar(self, client_id: UUID, clave: SecretStr) -> bool:
        """Guarda la clave cifrada. `False` si el despliegue no puede cifrar.

        NO GUARDAR es el desenlace correcto cuando falta la llave: guardar en claro seria
        cambiar una molestia (volver a pedirla) por una credencial ajena legible en la base.
        """
        if not self._llave:
            log.warning("clave.no_se_guarda_sin_cifrado", client_id=str(client_id))
            return False
        try:
            cifrada = cifrar(clave.get_secret_value(), llave=self._llave)
        except CifradoNoConfiguradoError:
            return False
        await self._clients.guardar_clave(client_id, cifrada)  # type: ignore[attr-defined]
        log.info("clave.guardada", client_id=str(client_id))
        return True

    async def recuperar(self, client_id: UUID) -> SecretStr | None:
        """La clave guardada, o None si no hay o no se puede descifrar.

        Un fallo al descifrar NO se propaga como error: significa que la llave del despliegue
        cambio, y lo que corresponde es comportarse como si no hubiera clave —pedirla de
        nuevo— en vez de tumbar la operacion con un problema de infraestructura que quien
        opera no puede resolver.
        """
        cifrada = await self._clients.leer_clave(client_id)  # type: ignore[attr-defined]
        if not cifrada or not self._llave:
            return None
        try:
            return SecretStr(descifrar(cifrada, llave=self._llave))
        except Exception:
            log.warning("clave.ilegible", client_id=str(client_id))
            return None

    async def olvidar(self, client_id: UUID) -> bool:
        """Borra la clave guardada. `False` si no habia ninguna."""
        borrada = await self._clients.borrar_clave(client_id)  # type: ignore[attr-defined]
        if borrada:
            log.info("clave.olvidada", client_id=str(client_id))
        return borrada
