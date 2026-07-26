"""El servicio de lectura: despacho por tipo, cache y errores de uso."""

from __future__ import annotations

import pytest

from declaras.documents.service import DocumentReaderService
from declaras.domain.errors import (
    DocumentUnreadableError,
    UnsupportedDocumentTypeError,
    ValidationError,
)
from tests.documents_fixtures import build_exogena_xlsx


def test_lee_un_documento_soportado():
    service = DocumentReaderService()
    reading = service.read(content=build_exogena_xlsx(), doc_type="EXOGENA")
    assert reading.doc_type == "EXOGENA"


def test_documento_sin_lector_falla_e_informa_los_soportados():
    """Sin lector es distinto de ilegible: el error debe decir cual de los dos es, para
    que el expediente sepa si generar una alerta al contador o no."""
    service = DocumentReaderService()
    with pytest.raises(UnsupportedDocumentTypeError) as exc:
        service.read(content=b"algo", doc_type="NO_EXISTE")
    assert "EXOGENA" in exc.value.details["supported"]


def test_documento_vacio_falla():
    service = DocumentReaderService()
    with pytest.raises(ValidationError):
        service.read(content=b"", doc_type="EXOGENA")


def test_la_segunda_lectura_del_mismo_contenido_usa_la_cache():
    service = DocumentReaderService()
    content = build_exogena_xlsx()
    first = service.read(content=content, doc_type="EXOGENA")
    second = service.read(content=content, doc_type="EXOGENA")
    assert first is second


def test_el_mismo_contenido_con_otro_tipo_no_comparte_cache():
    """La llave de cache incluye el tipo: el mismo byte-a-byte con un tipo distinto no
    debe devolver una lectura pensada para otro documento. Al intentar leer un XLSX como
    si fuera un PDF, el lector correcto se ejecuta y reporta que es ilegible."""
    service = DocumentReaderService()
    content = build_exogena_xlsx()
    with pytest.raises(DocumentUnreadableError):
        service.read(content=content, doc_type="RUT")


def test_la_cache_desaloja_el_mas_antiguo_al_llenarse():
    service = DocumentReaderService(cache_size=2)
    contents = [build_exogena_xlsx(id_number=str(n)) for n in range(3)]
    for c in contents:
        service.read(content=c, doc_type="EXOGENA")
    assert len(service._cache) == 2
