"""Los repositorios del expediente: donde un bug significa perder el rastro de un caso."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.case_repository import SqlCaseRepository, SqlClientRepository
from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.domain.case import CaseDocumentSource, CaseStatus, FlagSeverity
from declaras.domain.errors import CaseAlreadyExistsError, CaseNotFoundError, FlagNotFoundError
from declaras.domain.models import IdDocumentKind


@pytest.fixture
async def repos(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'case.db'}")
    await create_schema(engine)
    sessions = create_session_factory(engine)
    yield SqlClientRepository(sessions), SqlCaseRepository(sessions)
    await engine.dispose()


async def test_get_or_create_no_duplica_el_mismo_documento(repos):
    clients, _ = repos
    first = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    second = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    assert first.id == second.id


async def test_dos_documentos_distintos_son_dos_clientes(repos):
    clients, _ = repos
    a = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    b = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="456")
    assert a.id != b.id


async def test_no_se_puede_abrir_dos_expedientes_del_mismo_cliente_y_anio(repos):
    clients, cases = repos
    client = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    await cases.create(client_id=client.id, tax_year=2025)
    with pytest.raises(CaseAlreadyExistsError):
        await cases.create(client_id=client.id, tax_year=2025)


async def test_el_mismo_cliente_puede_tener_expedientes_de_anios_distintos(repos):
    clients, cases = repos
    client = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    await cases.create(client_id=client.id, tax_year=2024)
    await cases.create(client_id=client.id, tax_year=2025)
    assert {c.tax_year for c in await cases.list_for_client(client.id)} == {2024, 2025}


async def test_el_detalle_trae_cliente_documentos_flags_y_bitacora(repos):
    clients, cases = repos
    client = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123", full_name="X")
    case = await cases.create(client_id=client.id, tax_year=2025)

    doc = await cases.add_document(
        case_id=case.id,
        doc_type="EXOGENA",
        source=CaseDocumentSource.DIAN_PORTAL,
        storage_uri="file://x",
        filename="x.xlsx",
        content_sha256="abc",
    )
    await cases.add_flag(case_id=case.id, code="X", message="algo", source_document_id=doc.id)
    await cases.add_event(case_id=case.id, kind="TEST", message="paso algo")

    detail = await cases.get_detail(case.id)
    assert detail is not None
    assert detail.client.full_name == "X"
    assert len(detail.documents) == 1
    assert len(detail.flags) == 1
    assert len(detail.events) == 1
    assert detail.open_flags == detail.flags, "el flag recien creado no esta resuelto"


async def test_resolver_un_flag_lo_saca_de_los_abiertos(repos):
    clients, cases = repos
    client = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    case = await cases.create(client_id=client.id, tax_year=2025)
    flag = await cases.add_flag(
        case_id=case.id, code="X", message="algo", severity=FlagSeverity.INFO
    )

    await cases.resolve_flag(flag.id, note="ya se reviso")
    detail = await cases.get_detail(case.id)
    assert detail is not None
    assert detail.open_flags == []
    assert detail.flags[0].resolution_note == "ya se reviso"


async def test_resolver_un_flag_inexistente_falla(repos):
    _, cases = repos
    with pytest.raises(FlagNotFoundError):
        await cases.resolve_flag(__import__("uuid").uuid4())


async def test_agregar_documento_a_un_expediente_inexistente_falla(repos):
    _, cases = repos
    with pytest.raises(CaseNotFoundError):
        await cases.add_document(
            case_id=__import__("uuid").uuid4(),
            doc_type="RUT",
            source=CaseDocumentSource.CLIENT_UPLOAD,
            storage_uri="x",
            filename="x",
            content_sha256="x",
        )


async def test_transicion_de_estado(repos):
    clients, cases = repos
    client = await clients.get_or_create(id_kind=IdDocumentKind.CC, id_number="123")
    case = await cases.create(client_id=client.id, tax_year=2025)
    assert case.status is CaseStatus.OPEN

    updated = await cases.transition(case.id, status=CaseStatus.READY_FOR_REVIEW)
    assert updated.status is CaseStatus.READY_FOR_REVIEW
