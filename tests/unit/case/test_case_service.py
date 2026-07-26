"""El servicio del expediente: donde el conector DIAN, la lectura de documentos y el
expediente del cliente se amarran. Es la pieza que hacia falta para la consola."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.case_repository import SqlCaseRepository, SqlClientRepository
from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.adapters.storage.local import LocalDocumentStore
from declaras.documents.service import DocumentReaderService
from declaras.domain.case import CaseDocumentSource, CaseStatus, FlagSeverity
from declaras.domain.errors import ValidationError
from declaras.domain.models import (
    DocumentFailure,
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


@pytest.fixture
async def service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
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


def _succeeded_job(
    *, documents: list[StoredDocument], failures: list[DocumentFailure] | None = None
) -> Job:
    now = datetime.now(UTC)
    result = ExtractionResult(
        taxpayer=TaxpayerRef(id_number="1020304050", tax_year=2025),
        documents=documents,
        failures=failures or [],
        started_at=now,
        finished_at=now,
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


async def test_abrir_expediente_crea_el_cliente_la_primera_vez(service):
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    assert detail.case.status is CaseStatus.OPEN
    assert detail.client.id_number == "10203040"
    assert any(e.kind == "CASE_OPENED" for e in detail.events)


async def test_vincular_una_extraccion_exitosa_registra_los_documentos(service):
    svc, store = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    taxpayer = TaxpayerRef(id_number="10203040", tax_year=2025)

    stored_exo = await store.put(
        taxpayer=taxpayer,
        document=RawDocument(
            doc_type=DocumentType.EXOGENA, filename="e.xlsx", content=build_exogena_xlsx()
        ),
        job_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored_exo])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    assert len(updated.documents) == 1
    assert updated.documents[0].source is CaseDocumentSource.DIAN_PORTAL
    assert updated.case.status is CaseStatus.READY_FOR_REVIEW
    assert any(e.kind == "EXTRACTION_LINKED" for e in updated.events)


async def test_un_documento_con_lector_queda_leido_automaticamente(service):
    """Este es el punto central: al vincular la extraccion, la exogena no solo queda
    guardada, tambien queda LEIDA, lista para que el motor tributario la consuma."""
    svc, store = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    taxpayer = TaxpayerRef(id_number="10203040", tax_year=2025)
    stored = await store.put(
        taxpayer=taxpayer,
        document=RawDocument(
            doc_type=DocumentType.EXOGENA,
            filename="e.xlsx",
            content=build_exogena_xlsx(taxpayer_name="RESTREPO"),
        ),
        job_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    reading = updated.documents[0].reading
    assert reading is not None
    assert reading.field("taxpayer_name") == "RESTREPO"


async def test_un_documento_sin_lector_todavia_se_guarda_sin_flag(service):
    """PRIOR_RETURN y SUGGESTED_RETURN (PDF de la declaracion) aun no tienen parser: el
    documento debe quedar disponible igual, sin que la ausencia de lector cuente como
    un problema del expediente."""
    svc, store = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    stored = await store.put(
        taxpayer=TaxpayerRef(id_number="10203040", tax_year=2025),
        document=RawDocument(
            doc_type=DocumentType.PRIOR_RETURN, filename="d.pdf", content=b"%PDF-fake"
        ),
        job_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    assert updated.documents[0].reading is None
    assert updated.open_flags == []


async def test_los_avisos_de_lectura_se_convierten_en_flags(service):
    """Un aviso del parser (por ejemplo, un nombre con caracteres danados) no debe
    quedar enterrado dentro del documento: el contador lo tiene que ver como flag."""
    svc, store = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    stored = await store.put(
        taxpayer=TaxpayerRef(id_number="10203040", tax_year=2025),
        document=RawDocument(
            doc_type=DocumentType.EXOGENA,
            filename="e.xlsx",
            content=build_exogena_xlsx(detail_rows=[]),
        ),
        job_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    assert any(f.code == "NO_REPORTED_ITEMS" for f in updated.open_flags)


async def test_una_falla_de_extraccion_se_reporta_como_flag(service):
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    job = _succeeded_job(
        documents=[],
        failures=[
            DocumentFailure(
                doc_type=DocumentType.EXOGENA,
                code="DIAN_DOCUMENT_UNAVAILABLE",
                message="aun no publicada",
                retryable=False,
            )
        ],
    )
    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    flag = updated.open_flags[0]
    assert flag.severity is FlagSeverity.BLOCKING
    assert "EXOGENA" in flag.message


async def test_una_falla_reintentable_es_solo_una_advertencia_no_bloqueante(service):
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    job = _succeeded_job(
        documents=[],
        failures=[
            DocumentFailure(
                doc_type=DocumentType.EXOGENA,
                code="DIAN_PORTAL_UNAVAILABLE",
                message="el portal esta caido",
                retryable=True,
            )
        ],
    )
    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    assert updated.open_flags[0].severity is FlagSeverity.WARNING


async def test_no_se_puede_vincular_un_job_que_no_termino(service):
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    now = datetime.now(UTC)
    job = Job(
        id=uuid4(),
        kind=JobKind.DIAN_EXTRACTION,
        status=JobStatus.RUNNING,
        request={},
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValidationError):
        await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)


async def test_el_cliente_puede_subir_un_documento_por_chat(service):
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    updated = await svc.add_client_upload(
        case_id=detail.case.id,
        doc_type="certificado_intereses_vivienda",
        content=b"contenido de la foto",
        filename="certificado.jpg",
    )
    assert len(updated.documents) == 1
    doc = updated.documents[0]
    assert doc.doc_type == "certificado_intereses_vivienda"
    assert doc.source is CaseDocumentSource.CLIENT_UPLOAD
    assert any(e.kind == "DOCUMENT_UPLOADED" for e in updated.events)


async def test_un_documento_del_cliente_con_lector_tambien_se_lee(service):
    """Si el cliente sube algo que el sistema si sabe leer (por ejemplo, el mismo XLSX
    de exogena reenviado a mano), se lee igual que si viniera del conector."""
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    updated = await svc.add_client_upload(
        case_id=detail.case.id,
        doc_type="EXOGENA",
        content=build_exogena_xlsx(taxpayer_name="SUBIDO A MANO"),
        filename="e.xlsx",
    )
    assert updated.documents[0].reading is not None
    assert updated.documents[0].reading.field("taxpayer_name") == "SUBIDO A MANO"
