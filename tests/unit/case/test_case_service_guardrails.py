"""Protecciones del expediente.

Cada prueba de este archivo corresponde a un bug real que existio y se corrigio: son las
barreras que evitan que el expediente se contamine o que un problema pase inadvertido.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.case_repository import SqlCaseRepository, SqlClientRepository
from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.adapters.storage.local import LocalDocumentStore
from declaras.documents.service import DocumentReaderService
from declaras.domain.case import FlagSeverity
from declaras.domain.errors import TaxpayerMismatchError
from declaras.domain.models import (
    DocumentType,
    ExtractionResult,
    IdDocumentKind,
    Job,
    JobKind,
    JobStatus,
    RawDocument,
    StoredDocument,
    TaxpayerRef,
)
from declaras.services.case_service import CaseService
from tests.documents_fixtures import build_exogena_xlsx

CLIENT_ID_NUMBER = "11111111"
OTHER_ID_NUMBER = "99999999"


@pytest.fixture
async def service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}")
    await create_schema(engine)
    sessions = create_session_factory(engine)
    store = LocalDocumentStore(tmp_path / "documents")
    yield (
        CaseService(
            clients=SqlClientRepository(sessions),
            cases=SqlCaseRepository(sessions),
            store=store,
            reader=DocumentReaderService(),
        ),
        store,
    )
    await engine.dispose()


def _job_for(taxpayer: TaxpayerRef, documents: list[StoredDocument]) -> Job:
    now = datetime.now(UTC)
    result = ExtractionResult(
        taxpayer=taxpayer, documents=documents, failures=[], started_at=now, finished_at=now
    )
    return Job(
        id=uuid4(),
        kind=JobKind.DIAN_EXTRACTION,
        status=JobStatus.SUCCEEDED,
        request={},
        result=result.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )


async def _stored_exogena(store, *, id_number: str, tax_year: int = 2025) -> StoredDocument:
    return await store.put(
        taxpayer=TaxpayerRef(id_number=id_number, tax_year=tax_year),
        document=RawDocument(
            doc_type=DocumentType.EXOGENA,
            filename="e.xlsx",
            content=build_exogena_xlsx(id_number=id_number),
        ),
        scope_id=uuid4(),
    )


async def test_no_se_vincula_una_extraccion_de_otro_contribuyente(service):
    """Mezclar la informacion tributaria de dos personas es el peor dano posible aqui:
    seria muy dificil de detectar despues y contaminaria el calculo del impuesto."""
    svc, store = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    ajeno = TaxpayerRef(id_number=OTHER_ID_NUMBER, tax_year=2025)
    doc = await _stored_exogena(store, id_number=OTHER_ID_NUMBER)

    with pytest.raises(TaxpayerMismatchError):
        await svc.link_extraction_result(
            case_id=case.case.id, extraction_job=_job_for(ajeno, [doc])
        )

    sin_cambios = await svc.get_detail(case.case.id)
    assert sin_cambios.documents == [], "el expediente no debe quedar tocado"


async def test_no_se_vincula_una_extraccion_de_otro_anio_gravable(service):
    svc, store = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    otro_anio = TaxpayerRef(id_number=CLIENT_ID_NUMBER, tax_year=2024)
    doc = await _stored_exogena(store, id_number=CLIENT_ID_NUMBER, tax_year=2024)

    with pytest.raises(TaxpayerMismatchError):
        await svc.link_extraction_result(
            case_id=case.case.id, extraction_job=_job_for(otro_anio, [doc])
        )


async def test_vincular_el_mismo_job_dos_veces_no_duplica_nada(service):
    """El agente puede reintentar la llamada por un timeout: la segunda vez debe ser
    inofensiva, no dejar el expediente con todo duplicado."""
    svc, store = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    taxpayer = TaxpayerRef(id_number=CLIENT_ID_NUMBER, tax_year=2025)
    job = _job_for(taxpayer, [await _stored_exogena(store, id_number=CLIENT_ID_NUMBER)])

    primero = await svc.link_extraction_result(case_id=case.case.id, extraction_job=job)
    segundo = await svc.link_extraction_result(case_id=case.case.id, extraction_job=job)

    assert len(primero.documents) == 1
    assert len(segundo.documents) == 1
    assert len(segundo.events) == len(primero.events), "tampoco se duplica la bitacora"


async def test_dos_jobs_distintos_con_el_mismo_documento_no_lo_duplican(service):
    """Si se re-extrae y el portal devuelve el mismo archivo byte a byte, el expediente
    no debe terminar con el documento dos veces."""
    svc, store = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    taxpayer = TaxpayerRef(id_number=CLIENT_ID_NUMBER, tax_year=2025)
    doc = await _stored_exogena(store, id_number=CLIENT_ID_NUMBER)

    await svc.link_extraction_result(case_id=case.case.id, extraction_job=_job_for(taxpayer, [doc]))
    segundo = await svc.link_extraction_result(
        case_id=case.case.id, extraction_job=_job_for(taxpayer, [doc])
    )
    assert len(segundo.documents) == 1


async def test_un_documento_ilegible_genera_flag_bloqueante(service):
    """Que un archivo este corrupto no puede pasar en silencio: el contador tiene que
    saber que hay que volver a pedirlo."""
    svc, _ = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    detail = await svc.add_client_upload(
        case_id=case.case.id,
        doc_type="EXOGENA",
        content=b"esto no es un xlsx",
        filename="corrupto.xlsx",
    )

    flags = [f for f in detail.open_flags if f.code == "DOCUMENT_UNREADABLE"]
    assert flags, "un documento ilegible debe generar flag"
    assert flags[0].severity is FlagSeverity.BLOCKING
    assert detail.documents[0].reading is None


async def test_un_tipo_sin_lector_todavia_no_genera_flag(service):
    """La ausencia de parser es una limitacion conocida del sistema, no un problema del
    documento: no debe ensuciar el expediente con alertas."""
    svc, _ = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    detail = await svc.add_client_upload(
        case_id=case.case.id,
        doc_type="certificado_prepagada",
        content=b"una foto cualquiera",
        filename="cert.jpg",
    )
    assert detail.open_flags == []
    assert detail.documents[0].reading is None


async def test_un_documento_a_nombre_de_otra_persona_genera_flag_bloqueante(service):
    """Caso real del producto: el cliente sube por error el certificado de su pareja.
    Ese valor no puede entrar al calculo sin que alguien lo mire."""
    svc, _ = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    detail = await svc.add_client_upload(
        case_id=case.case.id,
        doc_type="EXOGENA",
        content=build_exogena_xlsx(id_number=OTHER_ID_NUMBER),
        filename="ajena.xlsx",
    )

    flags = [f for f in detail.open_flags if f.code == "DOCUMENT_IDENTITY_MISMATCH"]
    assert flags, "un documento de otra persona debe frenar el expediente"
    assert flags[0].severity is FlagSeverity.BLOCKING
    assert OTHER_ID_NUMBER in flags[0].message


async def test_un_documento_del_titular_no_genera_flag_de_identidad(service):
    svc, _ = service
    case = await svc.open_case(id_kind=IdDocumentKind.CC, id_number=CLIENT_ID_NUMBER, tax_year=2025)
    detail = await svc.add_client_upload(
        case_id=case.case.id,
        doc_type="EXOGENA",
        content=build_exogena_xlsx(id_number=CLIENT_ID_NUMBER),
        filename="propia.xlsx",
    )
    assert [f for f in detail.open_flags if f.code == "DOCUMENT_IDENTITY_MISMATCH"] == []
