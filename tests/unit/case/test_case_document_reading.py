"""Lo que el expediente le tiene que dar al lector, y lo que hace con lo que le devuelve.

Vive aparte de `test_case_service.py` (que es de otro autor y no se toca) porque lo que se
prueba acá nació con la familia de lectores con modelo: hasta que existió, leer un documento
no necesitaba contexto del caso, no costaba dinero y no tardaba nada.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.case_repository import SqlCaseRepository, SqlClientRepository
from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.adapters.storage.local import LocalDocumentStore
from declaras.documents import registry
from declaras.documents.models import DocumentReading
from declaras.documents.service import DocumentReaderService
from declaras.domain.case import FlagSeverity
from declaras.domain.errors import DocumentReaderUnavailableError
from declaras.domain.models import IdDocumentKind
from declaras.services.case_service import CaseService

TAX_YEAR = 2025


@pytest.fixture
async def expediente(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'caso.db'}")
    await create_schema(engine)
    sessions = create_session_factory(engine)
    yield CaseService(
        clients=SqlClientRepository(sessions),
        cases=SqlCaseRepository(sessions),
        store=LocalDocumentStore(tmp_path / "documents"),
        reader=DocumentReaderService(),
    )
    await engine.dispose()


async def _subir_un_220(expediente, lector, monkeypatch):
    monkeypatch.setitem(registry.LLM_READERS, "CERT_INGRESOS_220", lector)
    detail = await expediente.open_case(
        id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=TAX_YEAR
    )
    return await expediente.add_client_upload(
        case_id=detail.case.id,
        doc_type="CERT_INGRESOS_220",
        content=b"%PDF-un certificado",
        filename="220.pdf",
    )


def _lectura_valida() -> DocumentReading:
    return DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="falso", content_sha256="abc"
    )


async def test_el_expediente_le_pasa_al_lector_el_anio_del_caso(expediente, monkeypatch):
    """El año gravable ya está en la mano al leer (`detail.case.tax_year`): si no baja hasta
    el lector, el guard del certificado del año equivocado no dispara nunca por este camino,
    que es el que está vivo."""
    vistos: list[int | None] = []

    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        vistos.append(anio_esperado)
        return _lectura_valida()

    await _subir_un_220(expediente, lector, monkeypatch)
    assert vistos == [TAX_YEAR]


async def test_una_falla_pasajera_del_lector_deja_alerta_en_vez_de_silencio(
    expediente, monkeypatch
):
    """El caso peor: el documento se guarda con su evento, sin lectura y sin alerta, y el
    contador ve un certificado que simplemente no tiene cifras. Como reintentar sí sirve, el
    aviso es de los que piden atención, no de los que dicen que el documento está malo."""

    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        raise DocumentReaderUnavailableError()

    updated = await _subir_un_220(expediente, lector, monkeypatch)

    assert updated.documents[0].reading is None
    (flag,) = updated.open_flags
    assert flag.code == "DOCUMENT_READER_UNAVAILABLE"
    assert flag.severity is FlagSeverity.WARNING
    assert flag.message.startswith("No se pudo leer el certificado de ingresos y retenciones")


async def test_la_lectura_no_corre_en_el_hilo_del_event_loop(expediente, monkeypatch):
    """Un lector con modelo tarda decenas de segundos en una llamada HTTP bloqueante. Si corre
    en el hilo del loop, no se demora esta request: se detienen TODAS, `/health` incluido, y
    también el worker de extracciones, que es una task del mismo loop — o sea que la consulta a
    la DIAN de otro cliente se congela mientras se lee este PDF."""
    hilos: list[int] = []

    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        hilos.append(threading.get_ident())
        return _lectura_valida()

    await _subir_un_220(expediente, lector, monkeypatch)
    assert hilos and hilos[0] != threading.get_ident()


