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


# El expediente valida que la extraccion sea del mismo contribuyente, asi que la cedula
# tiene que ser la misma en todo el archivo: se define una sola vez.
ID_NUMBER = "10203040"
TAX_YEAR = 2025


def _succeeded_job(
    *,
    documents: list[StoredDocument],
    failures: list[DocumentFailure] | None = None,
    id_number: str = ID_NUMBER,
    tax_year: int = TAX_YEAR,
) -> Job:
    now = datetime.now(UTC)
    result = ExtractionResult(
        taxpayer=TaxpayerRef(id_number=id_number, tax_year=tax_year),
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
        scope_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored_exo])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    assert len(updated.documents) == 1
    assert updated.documents[0].source is CaseDocumentSource.DIAN_PORTAL
    assert updated.case.status is CaseStatus.READY_FOR_REVIEW
    assert any(e.kind == "DIAN_QUERY" for e in updated.events)


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
        scope_id=uuid4(),
    )
    job = _succeeded_job(documents=[stored])

    updated = await svc.link_extraction_result(case_id=detail.case.id, extraction_job=job)
    reading = updated.documents[0].reading
    assert reading is not None
    assert reading.field("taxpayer_name") == "RESTREPO"


async def test_un_documento_sin_lector_todavia_se_guarda_sin_flag(service):
    """Un soporte que el cliente manda por chat (la foto de un recibo) no tiene lector
    deterministico: debe quedar guardado y disponible igual, sin que la ausencia de lector
    cuente como un problema del expediente. No tener lector no es lo mismo que no poder leer:
    lo primero es normal, lo segundo si es un flag."""
    svc, _ = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)

    updated = await svc.add_client_upload(
        case_id=detail.case.id,
        doc_type="recibo_de_pago",
        content=b"una foto cualquiera",
        filename="recibo.jpg",
    )
    assert updated.documents[0].reading is None
    assert updated.open_flags == []


async def test_una_declaracion_que_no_se_puede_leer_si_es_un_flag(service):
    """El 210 si tiene lector, asi que un PDF corrupto no puede pasar en silencio: si se
    guardara sin leer, el motor trabajaria sin la declaracion del anio anterior y nadie lo
    notaria hasta comparar el patrimonio."""
    svc, store = service
    detail = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    stored = await store.put(
        taxpayer=TaxpayerRef(id_number="10203040", tax_year=2025),
        document=RawDocument(
            doc_type=DocumentType.PRIOR_RETURN, filename="d.pdf", content=b"%PDF-roto"
        ),
        scope_id=uuid4(),
    )

    updated = await svc.link_extraction_result(
        case_id=detail.case.id, extraction_job=_succeeded_job(documents=[stored])
    )
    assert updated.documents[0].reading is None
    assert [f.severity for f in updated.open_flags] == [FlagSeverity.BLOCKING]


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
        scope_id=uuid4(),
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
    assert "exógena" in flag.message, "el mensaje debe nombrar el documento en lenguaje llano"
    assert "aun no publicada" in flag.message


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


# ─────── que trajo la consulta ───────
#
# La DIAN incrusta la fecha de generacion en cada archivo, asi que el mismo documento
# descargado dos veces tiene contenido y hash distintos. Sin comparar lo que dice el
# documento, cada consulta parece traer todo de nuevo.


async def _consultar(svc, store, case_id, *, contenido):
    stored = await store.put(
        taxpayer=TaxpayerRef(id_number="10203040", tax_year=2025),
        document=RawDocument(doc_type=DocumentType.EXOGENA, filename="e.xlsx", content=contenido),
        scope_id=uuid4(),
    )
    return await svc.link_extraction_result(
        case_id=case_id, extraction_job=_succeeded_job(documents=[stored])
    )


def _ultima_consulta(detail):
    return next(e for e in reversed(detail.events) if e.kind == "DIAN_QUERY")


async def test_la_primera_consulta_dice_que_trajo_los_documentos(service):
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    assert "trajo" in _ultima_consulta(detail).message


async def test_volver_a_consultar_sin_novedades_lo_dice_en_vez_de_anunciar_una_actualizacion(
    service,
):
    """Es la queja mas legitima de quien vuelve a consultar: si el sistema anuncia lo mismo
    cada vez, parece que descargo todo de nuevo sin razon."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())

    evento = _ultima_consulta(detail)
    assert "no encontró cambios" in evento.message
    assert evento.payload["changed"] == []


async def test_una_fecha_de_generacion_distinta_no_cuenta_como_cambio(service):
    """El portal cambia la fecha del reporte en cada descarga sin que cambie ni un peso de lo
    reportado. Si eso contara como cambio, nunca se podria decir "no hubo cambios"."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    # El fixture escribe la fecha del reporte fija, asi que se cambia el contenido de forma
    # que solo el hash del archivo cambie.
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx() + b"\x00")
    assert "no encontró cambios" in _ultima_consulta(detail).message


async def test_cuando_la_dian_si_actualizo_algo_se_dice_que_fue(service):
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    detail = await _consultar(
        svc,
        store,
        caso.case.id,
        contenido=build_exogena_xlsx(
            thresholds={
                "ingresos": 99_000_000,
                "patrimonio": 5_000_000,
                "consumo_tarjeta": 1_000_000,
                "movimientos": 20_000_000,
                "compras": 500_000,
            }
        ),
    )
    evento = _ultima_consulta(detail)
    assert "cambió" in evento.message
    assert "exógena" in evento.message.lower()
    assert evento.payload["changed"] == ["EXOGENA"]


async def test_la_bitacora_no_muestra_codigos_internos(service):
    """Lo que se registra lo lee una persona. El codigo del evento existe para consultar la
    bitacora por tipo, no para mostrarlo."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    for evento in detail.events:
        assert evento.message
        assert evento.kind not in evento.message
        assert "_" not in evento.message


# Vocabulario que solo existe dentro del sistema. El producto es para que una persona resuelva
# su propia declaracion, y ninguna de estas palabras significa nada para ella.
VOCABULARIO_INTERNO = ("expediente", "flag", "parser", "job", "vincul", "extraccion")


async def test_lo_que_se_registra_no_usa_vocabulario_interno(service):
    """Paso dos veces: la palabra "expediente" se filtro a un evento que lee el cliente. Un caso
    que lo vigile cuesta menos que volver a encontrarlo en una captura de pantalla."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    await svc.add_client_upload(
        case_id=caso.case.id,
        doc_type="certificado_intereses_vivienda",
        content=b"una foto",
        filename="cert.jpg",
    )
    detail = await svc.get_detail(caso.case.id)

    textos = [e.message for e in detail.events] + [f.message for f in detail.flags]
    for texto in textos:
        for palabra in VOCABULARIO_INTERNO:
            assert palabra not in texto.lower(), f"{palabra!r} aparece en {texto!r}"


async def test_un_aviso_que_no_pide_nada_queda_como_constancia_no_como_pendiente(service):
    """El portal manda el nombre del contribuyente con caracteres danados. No hay nada que
    nadie pueda hacer y no afecta ninguna cifra, asi que no puede estar en la misma lista que
    un valor que hay que confirmar: mezclarlos le quita autoridad a la lista."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    detail = await _consultar(
        svc, store, caso.case.id, contenido=build_exogena_xlsx(taxpayer_name="PEREZ JOS\ufffd")
    )

    constancia = next(f for f in detail.flags if f.code == "TEXT_ENCODING_DAMAGED")
    assert constancia.severity is FlagSeverity.INFO


async def test_volver_a_consultar_no_deja_un_evento_por_documento(service):
    """Una consulta reemplaza los cinco documentos a la vez. Cinco lineas seguidas diciendo lo
    mismo tapan lo que si paso."""
    svc, store = service
    caso = await svc.open_case(id_kind=IdDocumentKind.CC, id_number="10203040", tax_year=2025)
    await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())
    detail = await _consultar(svc, store, caso.case.id, contenido=build_exogena_xlsx())

    consultas = [e for e in detail.events if e.kind == "DIAN_QUERY"]
    assert len(consultas) == 2
    # El reemplazo queda registrado, pero contado en el evento de la consulta.
    assert consultas[-1].payload["superseded"] == 1
    assert not [e for e in detail.events if e.kind == "DOCUMENT_SUPERSEDED"]
