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


# ─────────────── de donde sale cada tope ───────────────
#
# Un tope es una cifra que decide si alguien esta obligado a declarar. Poder abrirla y ver
# quien reporto cada peso es la diferencia entre auditarla y tener que creerla.


def _fila_tope(**cambios):
    fila = {
        "reporter_nit": "890903938",
        "reporter_name": "BANCOLOMBIA S.A.",
        "concept": "Total consumos o gastos con tarjeta Crédito o Débito",
        "amount": 2_384_279,
        "suggested_use": "Tope 3: Consumos TC",
    }
    return fila | cambios


def test_cada_tope_dice_quien_reporto_cada_valor():
    detail = _detail_with_exogena(
        thresholds={
            "consumo_tarjeta": 17_963_118,
            "ingresos": 0,
            "patrimonio": 0,
            "movimientos": 0,
            "compras": 0,
        },
        detail_rows=[
            _fila_tope(),
            _fila_tope(reporter_name="NU COLOMBIA", amount=15_578_839),
        ],
    )
    summary = build_summary(detail)
    tarjeta = next(
        p for p in summary.threshold_provenance if p.code == ThresholdCode.CONSUMO_TARJETA
    )

    # Ordenado de mayor a menor: es el orden en que alguien revisa de donde sale una cifra.
    assert [s.reporter_name for s in tarjeta.sources] == ["NU COLOMBIA", "BANCOLOMBIA S.A."]
    assert tarjeta.added_total == 17_963_118
    assert tarjeta.reconciles


def test_una_fuente_que_la_dian_compara_no_se_suma_al_tope():
    """La DIAN no siempre suma: para el patrimonio compara lo reportado contra lo que el
    contribuyente declaro el ano anterior y toma el mayor. Sumarla inflaria el tope."""
    detail = _detail_with_exogena(
        thresholds={
            "patrimonio": 4_000_000,
            "ingresos": 0,
            "consumo_tarjeta": 0,
            "movimientos": 0,
            "compras": 0,
        },
        detail_rows=[
            _fila_tope(
                concept="Saldo cuentas bancarias (Titular Principal)",
                amount=4_000_000,
                suggested_use="Tope 2: Patrimonio | R29 Patrimonio Bruto",
            ),
            _fila_tope(
                reporter_name="PEREZ GOMEZ ANA MARIA",
                concept="Total patrimonio bruto declarado en el año anterior",
                amount=1_880_000,
                suggested_use="El Tope 2 - Patrimonio, toma el mayor valor entre lo reportado "
                "y lo declarado el año anterior",
            ),
        ],
    )
    patrimonio = next(
        p for p in build_summary(detail).threshold_provenance if p.code == ThresholdCode.PATRIMONIO
    )
    assert patrimonio.added_total == 4_000_000
    assert patrimonio.reconciles
    comparada = next(s for s in patrimonio.sources if s.compared_not_added)
    assert comparada.amount == 1_880_000


def test_una_diferencia_que_las_fuentes_no_explican_queda_a_la_vista():
    """Pasa con datos reales: el tope que declara la DIAN no siempre es exactamente la suma de
    lo que ella misma etiqueto. Se muestra la diferencia en vez de taparla con una formula
    inventada, porque quien firma la declaracion necesita saberlo."""
    detail = _detail_with_exogena(
        thresholds={
            "consumo_tarjeta": 2_384_558,
            "ingresos": 0,
            "patrimonio": 0,
            "movimientos": 0,
            "compras": 0,
        },
        detail_rows=[_fila_tope()],
    )
    tarjeta = next(
        p
        for p in build_summary(detail).threshold_provenance
        if p.code == ThresholdCode.CONSUMO_TARJETA
    )
    assert not tarjeta.reconciles
    assert tarjeta.unexplained_difference == 279
    # El valor que gobierna sigue siendo el de la DIAN, no la suma de las fuentes.
    assert tarjeta.official_amount == 2_384_558


def test_el_tope_dice_cuanto_de_su_valor_es_de_otra_persona():
    """Es lo que permite contestar "y si ese ingreso no es mio?": no basta con avisar, hay que
    poder decir cuanto del tope depende de ese valor en duda."""
    detail = _detail_with_exogena(
        taxpayer_name="VALENCIA MORENO JUAN JOSE",
        thresholds={
            "ingresos": 11_570_000,
            "patrimonio": 0,
            "consumo_tarjeta": 0,
            "movimientos": 0,
            "compras": 0,
        },
        detail_rows=[
            _fila_tope(
                reporter_name="ZPN ARQUIREDES SAS",
                reported_name="Alejandra Delgado Bautista",
                concept="Servicios (Concepto: 5004)",
                amount=7_330_000,
                suggested_use="Tope 1: Ingresos brutos | R43 Ingresos brutos",
            ),
            _fila_tope(
                reporter_name="INVERSIONES MCN SAS",
                concept="Pagos por salarios (Concepto: 2276)",
                amount=4_240_000,
                suggested_use="Tope 1: Ingresos brutos | R32 Ingresos brutos",
            ),
        ],
    )
    ingresos = next(
        p for p in build_summary(detail).threshold_provenance if p.code == ThresholdCode.INGRESOS
    )
    assert ingresos.amount_not_from_titular == 7_330_000
    assert ingresos.added_total == 11_570_000


def test_un_tope_sin_valores_reportados_no_aparece_en_la_procedencia():
    """Los cinco topes siempre se evaluan (aunque den cero), pero mostrar "de donde sale" un
    tope del que nadie reporto nada seria una tarjeta vacia."""
    detail = _detail_with_exogena(detail_rows=[_fila_tope()])
    codigos = {p.code for p in build_summary(detail).threshold_provenance}
    assert codigos == {ThresholdCode.CONSUMO_TARJETA}
