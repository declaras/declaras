"""Servicio de lectura de documentos.

A diferencia de la extraccion en el portal, leer un documento toma segundos: no hay jobs
ni cola, la operacion es sincronica. Lo que si hay es cache por contenido, para no releer
(o no pagarle dos veces a un modelo de vision, cuando ese camino exista) la misma foto.
"""

from __future__ import annotations

import hashlib

from declaras.documents.models import DocumentReading
from declaras.documents.registry import reader_for, supported_types
from declaras.domain.errors import ValidationError
from declaras.observability import get_logger

log = get_logger(__name__)


class DocumentReaderService:
    def __init__(self, *, cache_size: int = 256) -> None:
        self._cache: dict[str, DocumentReading] = {}
        self._cache_size = cache_size

    def read(self, *, content: bytes, doc_type: str) -> DocumentReading:
        """Lee un documento de la clase indicada.

        El tipo se recibe por parametro y no se adivina: en el flujo del producto el
        agente conversacional siempre sabe que documento pidio (el gap analysis dispara
        la pregunta y el tipo esperado juntos), asi que informarlo elimina una fuente de
        error. La deteccion automatica queda para cuando el cliente manda algo que nadie
        le pidio, y vive aparte en `sniff.py`.
        """
        if not content:
            raise ValidationError("el documento esta vacio")

        digest = hashlib.sha256(content).hexdigest()
        key = f"{doc_type}:{digest}"
        cached = self._cache.get(key)
        if cached is not None:
            log.info("documents.read.cache_hit", doc_type=doc_type)
            return cached

        reader = reader_for(doc_type)
        if reader is None:
            raise ValidationError(
                f"no hay lector para {doc_type}",
                doc_type=doc_type,
                supported=supported_types(),
            )

        reading = reader(content)
        self._remember(key, reading)
        return reading

    def _remember(self, key: str, reading: DocumentReading) -> None:
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = reading
