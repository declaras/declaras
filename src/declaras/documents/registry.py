"""Registro de lectores de documentos.

Cada clase de documento tiene un lector. Hay dos familias y conviene no mezclarlas:

  Documentos del portal   estructurados y estables (XLSX con columnas fijas, formularios
                          con casillas numeradas). Se leen con parsers deterministicos:
                          cero IA, resultado reproducible y testeable.
  Documentos del cliente  fotos y PDFs heterogeneos (cada banco tiene su formato). Se leen
                          con un modelo de vision, que estima y por eso reporta confianza.

Agregar un documento nuevo es escribir un lector y registrarlo aqui. El tipo se recibe por
parametro (el agente conversacional siempre sabe que pidio), y la deteccion automatica
queda para cuando el cliente manda algo que nadie le pidio (ver `sniff.py`).
"""

from __future__ import annotations

from collections.abc import Callable

from declaras.documents.models import DocumentReading
from declaras.documents.parsers import einvoice_summary, exogena, renta_210, rut

Reader = Callable[[bytes], DocumentReading]

# Lectores deterministicos para los documentos que entrega el portal DIAN.
DETERMINISTIC_READERS: dict[str, Reader] = {
    "EXOGENA": exogena.parse,
    "RUT": rut.parse,
    "EINVOICE_SUMMARY": einvoice_summary.parse,
    # La declaracion presentada del anio anterior y el borrador del anio en curso son el
    # mismo formulario 210, asi que los lee el mismo parser.
    "PRIOR_RETURN": renta_210.parse,
    "SUGGESTED_RETURN": renta_210.parse,
}


def reader_for(doc_type: str) -> Reader | None:
    """Lector deterministico de una clase de documento, si existe."""
    return DETERMINISTIC_READERS.get(doc_type)


def is_deterministic(doc_type: str) -> bool:
    return doc_type in DETERMINISTIC_READERS


def supported_types() -> list[str]:
    return sorted(DETERMINISTIC_READERS)
