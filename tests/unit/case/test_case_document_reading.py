"""Lo que el expediente le tiene que dar al lector, y lo que hace con lo que le devuelve.

Vive aparte de `test_case_service.py` (que es de otro autor y no se toca) porque lo que se
prueba acá nació con la familia de lectores con modelo: hasta que existió, leer un documento
no necesitaba contexto del caso, no costaba dinero y no tardaba nada.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.case_repository import SqlCaseRepository, SqlClientRepository
from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.adapters.storage.local import LocalDocumentStore
from declaras.documents import registry
from declaras.documents.models import DocumentReading
from declaras.documents.service import DocumentReaderService
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


