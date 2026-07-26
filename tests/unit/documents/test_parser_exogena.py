"""El parser de exogena es la base del borrador: si falla, el motor calcula mal."""

from __future__ import annotations

from declaras.documents.parsers.exogena import parse
from tests.documents_fixtures import build_exogena_xlsx


def test_lee_el_encabezado_del_contribuyente():
    reading = parse(build_exogena_xlsx(id_number="1122334455", taxpayer_name="LOPEZ RUIZ"))
    assert reading.field("id_number") == "1122334455"
    assert reading.field("taxpayer_name") == "LOPEZ RUIZ"


def test_lee_los_cinco_topes():
    reading = parse(
        build_exogena_xlsx(
            thresholds={
                "ingresos": 111,
                "patrimonio": 222,
                "consumo_tarjeta": 333,
                "movimientos": 444,
                "compras": 555,
            }
        )
    )
    topes = {f.name: f.value for f in reading.fields if f.name.startswith("tope_")}
    assert topes == {
        "tope_ingresos": 111,
        "tope_patrimonio": 222,
        "tope_consumo_tarjeta": 333,
        "tope_movimientos": 444,
        "tope_compras": 555,
    }


def test_cada_fila_trae_el_renglon_del_210_que_indica_la_dian():
    """Esto es lo que ahorra el trabajo mas duro del motor: la DIAN ya dice a que
    casilla va cada valor reportado."""
    reading = parse(
        build_exogena_xlsx(
            detail_rows=[
                {
                    "reporter_nit": "900111222",
                    "reporter_name": "X SAS",
                    "concept": "Otros ingresos (Concepto: 5016)",
                    "amount": 800_000,
                    "suggested_use": "Tope 1: Ingresos brutos | R74 Ingresos brutos",
                }
            ]
        )
    )
    row = reading.rows[0]
    assert row.values["concept_code"] == "5016"
    assert row.values["form_lines"] == [74]
    assert row.values["thresholds"] == ["ingresos"]
    assert row.values["amount"] == 800_000


def test_una_fila_puede_contar_para_varios_renglones_a_la_vez():
    reading = parse(
        build_exogena_xlsx(
            detail_rows=[
                {
                    "reporter_nit": "800197268",
                    "reporter_name": "FONDO CESANTIAS",
                    "concept": "Cesantias",
                    "amount": 100,
                    "suggested_use": "R29 R32 R36 R51 R67 R84",
                }
            ]
        )
    )
    assert reading.rows[0].values["form_lines"] == [29, 32, 36, 51, 67, 84]


def test_sin_conceptos_reportados_avisa_pero_no_falla():
    reading = parse(build_exogena_xlsx(detail_rows=[]))
    assert reading.rows == []
    assert any(w.code == "NO_REPORTED_ITEMS" for w in reading.warnings)


def test_los_valores_deterministicos_tienen_confianza_total():
    from declaras.documents.models import Confidence

    reading = parse(build_exogena_xlsx())
    assert all(f.confidence == Confidence.DETERMINISTIC for f in reading.fields)


def test_el_hash_del_contenido_es_estable():
    content = build_exogena_xlsx()
    assert parse(content).content_sha256 == parse(content).content_sha256
