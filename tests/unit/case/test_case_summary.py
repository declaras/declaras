"""El resumen del expediente: lo que el sistema deriva de lo que leyo.

No calcula impuesto; organiza y compara con la ley lo que los documentos ya dicen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from declaras.documents.parsers.exogena import parse as parse_exogena
from declaras.domain.case import Case, CaseDetail, CaseDocument, CaseDocumentSource, Client
from declaras.domain.models import IdDocumentKind
from declaras.services.case_summary import build_summary
from declaras.tax.obligation import ThresholdCode
from tests.documents_fixtures import build_exogena_xlsx


def _detail_with_exogena(**xlsx_kwargs) -> CaseDetail:
    now = datetime.now(UTC)
    client = Client(id_kind=IdDocumentKind.CC, id_number="10203040", created_at=now, updated_at=now)
    case = Case(client_id=client.id, tax_year=2025, created_at=now, updated_at=now)
    reading = parse_exogena(build_exogena_xlsx(**xlsx_kwargs))
    document = CaseDocument(
        case_id=case.id,
        doc_type="EXOGENA",
        source=CaseDocumentSource.DIAN_PORTAL,
        storage_uri="file://x",
        filename="e.xlsx",
        content_sha256="abc",
        added_at=now,
        reading=reading,
    )
    return CaseDetail(case=case, client=client, documents=[document])


def test_sin_documentos_el_resumen_esta_vacio_pero_no_falla():
    now = datetime.now(UTC)
    client = Client(id_kind=IdDocumentKind.CC, id_number="10203040", created_at=now, updated_at=now)
    case = Case(client_id=client.id, tax_year=2025, created_at=now, updated_at=now)
    summary = build_summary(CaseDetail(case=case, client=client))
    assert not summary.has_data
    assert summary.obligation is None
    assert summary.form_lines == []


def test_evalua_la_obligacion_con_los_topes_de_la_exogena():
    detail = _detail_with_exogena(
        thresholds={
            "ingresos": 70_240_264,
            "patrimonio": 14_147_658,
            "consumo_tarjeta": 17_963_118,
            "movimientos": 99_512_480,
            "compras": 3_660_325,
        }
    )
    summary = build_summary(detail)
    assert summary.obligation is not None
    assert summary.obligation.is_obligated
    assert {t.code for t in summary.obligation.exceeded_thresholds} == {
        ThresholdCode.INGRESOS,
        ThresholdCode.MOVIMIENTOS,
    }


def test_quien_no_supera_topes_queda_como_no_obligado():
    detail = _detail_with_exogena(
        thresholds={
            "ingresos": 1_000_000,
            "patrimonio": 1_000_000,
            "consumo_tarjeta": 0,
            "movimientos": 0,
            "compras": 0,
        }
    )
    summary = build_summary(detail)
    assert summary.obligation is not None
    assert not summary.obligation.is_obligated


def test_agrega_los_valores_por_renglon_del_210():
    """La DIAN dice a que renglon va cada valor reportado; el resumen los suma."""
    detail = _detail_with_exogena(
        detail_rows=[
            {
                "reporter_nit": "900111222",
                "reporter_name": "A SAS",
                "concept": "Salario (Concepto: 5001)",
                "amount": 1_000_000,
                "suggested_use": "R32 Ingresos brutos",
            },
            {
                "reporter_nit": "900333444",
                "reporter_name": "B SAS",
                "concept": "Otro salario (Concepto: 5001)",
                "amount": 500_000,
                "suggested_use": "R32 Ingresos brutos",
            },
            {
                "reporter_nit": "900555666",
                "reporter_name": "C SAS",
                "concept": "Retencion (Concepto: 5004)",
                "amount": 90_000,
                "suggested_use": "R132 Retenciones",
            },
        ]
    )
    summary = build_summary(detail)
    por_renglon = {f.line: f for f in summary.form_lines}
    assert por_renglon[32].amount == 1_500_000
    assert por_renglon[32].concept_count == 2
    assert por_renglon[132].amount == 90_000


def test_un_valor_asignado_a_varios_renglones_suma_en_todos():
    """El reporte no dice como repartirlo, asi que se presenta completo en cada renglon
    para que un contador decida."""
    detail = _detail_with_exogena(
        detail_rows=[
            {
                "reporter_nit": "800197268",
                "reporter_name": "FONDO",
                "concept": "Cesantias",
                "amount": 200_000,
                "suggested_use": "R29 R32 R36",
            }
        ]
    )
    summary = build_summary(detail)
    assert {f.line: f.amount for f in summary.form_lines} == {29: 200_000, 32: 200_000, 36: 200_000}


def test_cuenta_documentos_leidos_y_pendientes():
    detail = _detail_with_exogena()
    now = datetime.now(UTC)
    detail.documents.append(
        CaseDocument(
            case_id=detail.case.id,
            doc_type="PRIOR_RETURN",
            source=CaseDocumentSource.DIAN_PORTAL,
            storage_uri="file://y",
            filename="d.pdf",
            content_sha256="def",
            added_at=now,
            reading=None,
        )
    )
    summary = build_summary(detail)
    assert summary.documents_read == 1
    assert summary.documents_pending_reading == 1
