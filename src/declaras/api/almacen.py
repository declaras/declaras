"""Almacén de casos en JSON plano. Demo: un archivo por caso, sin base de datos."""
import json
import os
import re
import uuid
from pathlib import Path

from pydantic import ValidationError

from declaras.caso import CasoTributario

# Alfabeto cerrado (hex) porque el id termina siendo un nombre de archivo. El router ya
# impide el traversal por `/` (un path param no lo matchea), así que esto NO es la defensa
# del HTTP: es el contrato del módulo. Cubre a cualquier otro llamador (scripts, un
# endpoint futuro, un id venido de un JSON) y los nombres raros que sí caen DENTRO del
# almacén — `..secreto`, `.oculto`, `nul` — que no son casos y confundirían un listado.
_ID_VALIDO = re.compile(r"[0-9a-f]{1,64}")


def _base() -> Path:
    # Se lee en cada llamada, no al importar: los tests apuntan el almacén a un tmp_path.
    return Path(os.environ.get("DECLARAS_DATOS", "var"))


def ruta_caso(caso_id: str) -> Path:
    """Ruta del caso. `KeyError` si el id no está en el alfabeto que genera `guardar`.

    "No existe" es la respuesta correcta (404) para un id que este almacén no pudo
    haber emitido: no es un error del servidor ni hay nada que buscar en disco.
    """
    if not _ID_VALIDO.fullmatch(caso_id):
        raise KeyError(caso_id)
    return _base() / "casos" / f"{caso_id}.json"


def ruta_documento(doc_id: str) -> Path:
    """Ruta del PDF de respaldo. El `doc_id` es el hash del extractor (`Fuente.ref`)."""
    if not _ID_VALIDO.fullmatch(doc_id):
        raise ValueError(f"doc_id inválido para un nombre de archivo: {doc_id!r}")
    return _base() / "documentos" / f"{doc_id}.pdf"


def _escribir(ruta: Path, contenido: bytes) -> None:
    """Escribe atómico: temporal en el mismo directorio y `os.replace`.

    Un `write_bytes` directo deja el archivo truncado si el proceso muere a mitad de
    escritura, y un caso a medias no se puede leer nunca más. Con `os.replace` el caso
    viejo sobrevive intacto o queda el nuevo completo, nunca un híbrido.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Nombre único: dos escrituras en paralelo no se pisan el temporal.
    tmp = ruta.with_name(f".{ruta.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(contenido)
        os.replace(tmp, ruta)  # atómico dentro del mismo filesystem
    finally:
        tmp.unlink(missing_ok=True)  # si el replace falló, no dejar basura


def guardar(caso: CasoTributario) -> str:
    caso_id = uuid.uuid4().hex[:12]
    reemplazar(caso_id, caso)
    return caso_id


def reemplazar(caso_id: str, caso: CasoTributario) -> None:
    _escribir(ruta_caso(caso_id), caso.model_dump_json(indent=2).encode("utf-8"))


def guardar_documento(doc_id: str, contenido: bytes) -> Path:
    """Persiste el PDF que respalda un hecho, para que `Fuente.ref` sea resoluble.

    Un `Fuente.ref` sin el documento detrás no es trazabilidad: es un string.
    """
    ruta = ruta_documento(doc_id)
    _escribir(ruta, contenido)
    return ruta


def existe(caso_id: str) -> bool:
    """¿Hay archivo para este id? NO valida el contenido, a propósito.

    Es el chequeo que necesita el `PUT`: el endpoint de reparación no puede exigir que
    el caso viejo sea legible, porque un caso ilegible es justo lo que va a arreglar.
    """
    try:
        return ruta_caso(caso_id).exists()
    except KeyError:
        return False


def cargar(caso_id: str) -> CasoTributario:
    """Lee el caso. `KeyError` si no existe; `ValueError` si el JSON ya no valida.

    Un caso escrito por una versión anterior del schema revienta contra
    `extra="forbid"`: eso es un error de datos del almacén, no una falla interna, y
    tampoco es "no existe" (responder 404 haría ver el caso como borrado).
    """
    ruta = ruta_caso(caso_id)
    if not ruta.exists():
        raise KeyError(caso_id)
    try:
        return CasoTributario.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError) as e:
        raise ValueError(
            f"el caso {caso_id} está guardado con un formato que ya no se pudo leer "
            f"(¿schema anterior?): {e}"
        ) from e
