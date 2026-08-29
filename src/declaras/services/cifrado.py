"""Cifrado de secretos que hay que poder LEER de vuelta.

La clave de la DIAN no se puede guardar como un hash: no se compara contra nada, se USA
para entrar al portal a nombre del contribuyente. Asi que el requisito no es "verificar",
es "recuperar", y eso obliga a cifrado reversible con una llave que vive FUERA de la base.

═══ POR QUE LA LLAVE VA EN EL ENTORNO Y NO EN LA BASE ═══

Todo el valor de cifrar esta en que quien se lleve un volcado de la base no se lleve las
claves. Si la llave viviera en la misma base, el volcado traeria las dos cosas y el cifrado
seria decoracion. Va en `DECLARAS_CLAVE_DE_CIFRADO`, que se administra donde se administran
los secretos del despliegue.

═══ LO QUE ESTO NO RESUELVE, DICHO CLARO ═══

Cifrar en reposo protege contra un volcado de la base y contra un backup extraviado. NO
protege contra alguien que entre al servidor con la aplicacion corriendo: ahi la llave esta
en memoria y las claves se descifran igual. Para eso hace falta un KMS externo donde la
operacion de descifrado ocurra fuera del proceso, y el puerto de este modulo no cambia
cuando se migre.

Se usa Fernet (AES-128-CBC + HMAC-SHA256) porque autentica ademas de cifrar: un texto
cifrado alterado se rechaza en vez de descifrarse a basura.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from declaras.domain.errors import DeclarasError


class CifradoNoConfiguradoError(DeclarasError):
    """Falta la llave y hay un secreto que guardar.

    Revienta en vez de guardar en claro: un fallback silencioso a texto plano es la forma
    de que un despliegue mal configurado escriba miles de claves legibles sin que nada avise.
    """

    code = "CIFRADO_NO_CONFIGURADO"
    http_status = 500
    default_message = (
        "No hay llave de cifrado configurada y se intentó guardar un secreto. "
        "Falta DECLARAS_CLAVE_DE_CIFRADO en el despliegue."
    )


class SecretoIlegibleError(DeclarasError):
    """El texto cifrado no abre con la llave actual: se roto o la llave cambio."""

    code = "SECRETO_ILEGIBLE"
    http_status = 500
    default_message = "Un dato cifrado no se pudo leer con la llave actual."


def _fernet(llave: str | None) -> Fernet:
    if not llave:
        raise CifradoNoConfiguradoError()
    # La llave del entorno es texto libre y Fernet exige 32 bytes en base64url. Derivarla con
    # SHA-256 acepta cualquier cadena sin obligar a que quien despliega genere un formato
    # exacto, que es donde la gente termina poniendo una llave debil por no pelear con el
    # formato.
    material = hashlib.sha256(llave.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def cifrar(valor: str, *, llave: str | None) -> str:
    """El valor cifrado, listo para guardar en una columna de texto."""
    return _fernet(llave).encrypt(valor.encode("utf-8")).decode("ascii")


def descifrar(cifrado: str, *, llave: str | None) -> str:
    """El valor original. Falla si el texto se alteró o la llave no es la que cifró."""
    try:
        return _fernet(llave).decrypt(cifrado.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretoIlegibleError() from exc
