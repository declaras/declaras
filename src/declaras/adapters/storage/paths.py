"""Convencion de rutas de almacenamiento.

El numero de documento no viaja en claro en la ruta: se usa un hash estable para no
esparcir datos personales por el bucket. La trazabilidad se mantiene por el job, que
si guarda la referencia del contribuyente.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from uuid import UUID

from declaras.domain.models import DocumentType, TaxpayerRef

_SUBJECT_HASH_LEN = 16


def subject_dir(taxpayer: TaxpayerRef) -> str:
    digest = hashlib.sha256(taxpayer.subject_key.encode()).hexdigest()[:_SUBJECT_HASH_LEN]
    return f"{taxpayer.id_kind.value.lower()}-{digest}"


# Una extension es un puñado de letras y numeros. Todo lo demas —barras, puntos, dos puntos—
# no describe un tipo de archivo: describe una RUTA, y una ruta dentro de la extension es como
# se sale alguien del directorio de almacenamiento.
_EXTENSION_LIMPIA = re.compile(r"^[A-Za-z0-9]{1,10}$")


def extension_for(filename: str, content_type: str) -> str:
    """La extension con la que se guarda el archivo, sin nada que pueda ser una ruta.

    EL NOMBRE LLEGA DE AFUERA: de lo que sube un cliente, o de la cabecera
    `Content-Disposition` que devuelve la DIAN. Un nombre como
    `doc.p/../../../otro-cliente/robado` produce, con un `rsplit` a secas, una "extension" que
    se lleva las barras consigo y termina componiendo una ruta.

    Hoy eso no alcanza a escaparse del directorio raiz, pero no por una verificacion sino por
    un accidente: el corte por el ultimo punto se come los `..` que harian el recorrido. Un
    nombre distinto podria no tener esa suerte, y de la seguridad de la escritura en disco no
    se puede depender de un accidente. Si la extension no parece una extension, no se usa.
    """
    if "." in filename:
        candidata = filename.rsplit(".", 1)[-1].lower()
        if _EXTENSION_LIMPIA.match(candidata):
            return candidata
    guessed = mimetypes.guess_extension(content_type) or ".bin"
    return guessed.lstrip(".")


def object_key(
    *,
    taxpayer: TaxpayerRef,
    doc_type: DocumentType,
    sha256: str,
    filename: str,
    content_type: str,
    scope_id: UUID,
) -> str:
    ext = extension_for(filename, content_type)
    parts = [
        subject_dir(taxpayer),
        str(taxpayer.tax_year),
        doc_type.value.lower(),
    ]
    if doc_type is DocumentType.EVIDENCE:
        parts.append(str(scope_id))
    parts.append(f"{sha256[:12]}.{ext}")
    return "/".join(parts)
