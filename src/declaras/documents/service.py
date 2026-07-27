"""Servicio de lectura de documentos.

A diferencia de la extraccion en el portal, leer un documento toma segundos: no hay jobs
ni cola, la operacion es sincronica. Lo que si hay es cache por contenido, para no releer
(o no pagarle dos veces a un modelo de vision, cuando ese camino exista) la misma foto.
"""

from __future__ import annotations

import hashlib
import threading

from declaras.documents.models import DocumentReading
from declaras.documents.registry import reader_for, supported_types
from declaras.domain.errors import UnsupportedDocumentTypeError, ValidationError
from declaras.observability import get_logger

log = get_logger(__name__)


class DocumentReaderService:
    def __init__(self, *, cache_size: int = 256) -> None:
        self._cache: dict[str, DocumentReading] = {}
        self._cache_size = cache_size
        self._candado = threading.Lock()

    def read(
        self, *, content: bytes, doc_type: str, anio_esperado: int | None = None
    ) -> DocumentReading:
        """Lee un documento de la clase indicada.

        El tipo se recibe por parametro y no se adivina: en el flujo del producto el
        agente conversacional siempre sabe que documento pidio (el gap analysis dispara
        la pregunta y el tipo esperado juntos), asi que informarlo elimina una fuente de
        error. La deteccion automatica queda para cuando el cliente manda algo que nadie
        le pidio, y vive aparte en `sniff.py`.

        `anio_esperado` es el anio gravable del caso, y para los lectores con modelo es un
        guard: rechazan el certificado que trae otro anio en vez de meter al expediente las
        cifras del anio equivocado. Quien lea sin contexto de caso puede omitirlo, y entonces
        ese guard no corre.
        """
        if not content:
            raise ValidationError("El documento llegó vacío.")

        digest = hashlib.sha256(content).hexdigest()
        # El anio entra a la clave porque cambia el RESULTADO: la misma lectura que es valida
        # para 2025 tiene que volver a fallar si se pide para 2024. Sin el, la primera lectura
        # buena se sirve para cualquier anio y el guard queda salteado por la cache.
        key = f"{doc_type}:{anio_esperado}:{digest}"
        cached = self._cache.get(key)
        if cached is not None:
            log.info("documents.read.cache_hit", doc_type=doc_type)
            return cached

        reader = reader_for(doc_type, anio_esperado=anio_esperado)
        if reader is None:
            raise UnsupportedDocumentTypeError(
                f"Todavía no hay lector para {doc_type}.",
                doc_type=doc_type,
                supported=supported_types(),
            )

        reading = reader(content)
        self._remember(key, reading)
        return reading

    def _remember(self, key: str, reading: DocumentReading) -> None:
        # Con candado porque `read` ya no corre solo en el hilo del event loop: los tres
        # llamadores la despachan al threadpool, asi que dos lecturas concurrentes pueden
        # elegir la MISMA clave mas vieja para desalojar y la segunda `pop` revienta con
        # KeyError. Ese KeyError no es un DeclarasError: saldria como 500 despues de haberle
        # pagado la lectura al modelo, y por el camino del expediente escaparia a
        # `_try_read_and_flag`, dejando el documento sin lectura y sin flag.
        with self._candado:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = reading
