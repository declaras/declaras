"""El servicio de lectura: despacho por tipo, cache y errores de uso."""

from __future__ import annotations

import contextlib
import threading

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


def test_dos_lecturas_concurrentes_no_se_pelean_el_desalojo():
    """`read` ya no corre solo en el hilo del event loop: los tres llamadores la despachan
    al threadpool. Sin candado, dos lecturas que llegan juntas con la cache llena eligen la
    MISMA clave para desalojar y la segunda `pop` revienta con KeyError — que no es un
    DeclarasError, asi que saldria como 500 despues de pagarle la lectura al modelo y
    dejaria el documento en el expediente sin lectura y sin flag.

    La barrera fuerza el entrelazado en vez de confiar en el azar del planificador: con
    candado el segundo hilo no llega a la barrera y esta expira; sin candado los dos entran
    con la misma clave elegida.
    """
    barrera = threading.Barrier(2)

    class CacheEntrelazada(dict):  # type: ignore[type-arg]
        """Cache que hace esperar a quien desaloja, para que los dos hilos coincidan."""

        def pop(self, *args: object) -> object:
            with contextlib.suppress(threading.BrokenBarrierError):
                barrera.wait(timeout=0.2)
            return super().pop(*args)  # type: ignore[arg-type]

    service = DocumentReaderService(cache_size=1)
    lectura = service.read(content=build_exogena_xlsx(), doc_type="EXOGENA")
    service._cache = CacheEntrelazada({"vieja": lectura})

    fallas: list[BaseException] = []

    def desalojar(n: int) -> None:
        try:
            service._remember(f"nueva-{n}", lectura)
        except BaseException as exc:
            fallas.append(exc)

    hilos = [threading.Thread(target=desalojar, args=(n,)) for n in (1, 2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=5)

    assert fallas == [], f"el desalojo concurrente reventó: {fallas!r}"
