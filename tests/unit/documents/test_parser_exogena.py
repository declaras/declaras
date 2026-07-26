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


# ─────── valores que un tercero reporto a nombre de otra persona ───────
#
# Es la pregunta del producto "y este ingreso que no es mio?". El reporte trae a quien le
# reporto cada tercero, asi que se puede contestar con datos en vez de suposiciones.


def _fila(**cambios):
    fila = {
        "reporter_nit": "900111222",
        "reporter_name": "ZPN ARQUIREDES SAS",
        "concept": "Servicios (Concepto: 5004)",
        "amount": 7_330_000,
        "suggested_use": "Tope 1: Ingresos brutos | R43 Ingresos brutos",
    }
    return fila | cambios


def test_avisa_cuando_un_tercero_reporta_a_nombre_de_otra_persona():
    """Caso real: la empresa reporto a la cedula correcta con el nombre de otra persona. Ese
    valor entra a los topes de obligacion como si fuera del titular."""
    reading = parse(
        build_exogena_xlsx(
            taxpayer_name="VALENCIA MORENO JUAN JOSE",
            detail_rows=[_fila(reported_name="Alejandra Delgado Bautista")],
        )
    )
    avisos = [w for w in reading.warnings if w.code == "REPORTED_TO_ANOTHER_PERSON"]
    assert len(avisos) == 1
    assert "Alejandra Delgado Bautista" in avisos[0].message
    assert "ZPN ARQUIREDES SAS" in avisos[0].message


def test_el_mismo_nombre_en_otro_orden_no_genera_aviso():
    """Los terceros escriben el nombre como quieren. Si esto avisara, el aviso perderia
    sentido: aparecería en casi todos los reportes y nadie lo miraria."""
    reading = parse(
        build_exogena_xlsx(
            taxpayer_name="VALENCIA MORENO JUAN JOSE",
            detail_rows=[_fila(reported_name="JUAN JOSE VALENCIA MORENO")],
        )
    )
    assert [w for w in reading.warnings if w.code == "REPORTED_TO_ANOTHER_PERSON"] == []


def test_un_nombre_con_acentos_danados_no_genera_aviso():
    """El portal manda el nombre con caracteres ilegibles. Comparar contra eso no puede
    producir un aviso falso."""
    reading = parse(
        build_exogena_xlsx(
            taxpayer_name="VALENCIA MORENO JUAN JOS\ufffd",
            detail_rows=[_fila(reported_name="VALENCIA MORENO JUAN JOSÉ")],
        )
    )
    assert [w for w in reading.warnings if w.code == "REPORTED_TO_ANOTHER_PERSON"] == []


def test_avisa_cuando_el_valor_se_reporto_a_otra_identificacion():
    """Numero distinto es una senal mas fuerte que un nombre distinto: el valor no es de
    este contribuyente."""
    reading = parse(
        build_exogena_xlsx(
            id_number="1000000001",
            detail_rows=[_fila(reported_id_number="9999999999")],
        )
    )
    avisos = [w for w in reading.warnings if w.code == "REPORTED_TO_ANOTHER_PERSON"]
    assert len(avisos) == 1
    assert "no debería contar como suyo" in avisos[0].message


def test_se_avisa_una_vez_por_tercero_no_una_por_fila():
    """Cuando un tercero confunde a una persona lo hace en todos los valores que le reporta.
    Un aviso por fila esconderia los demas pendientes del expediente."""
    reading = parse(
        build_exogena_xlsx(
            taxpayer_name="VALENCIA MORENO JUAN JOSE",
            detail_rows=[
                _fila(reported_name="Alejandra Delgado Bautista", amount=1_920_000),
                _fila(reported_name="Delgado Bautista Alejandra", amount=5_410_000),
                _fila(reported_name="Alejandra Delgado Bautista", amount=800_000),
            ],
        )
    )
    avisos = [w for w in reading.warnings if w.code == "REPORTED_TO_ANOTHER_PERSON"]
    assert len(avisos) == 1
    # El aviso suma lo que ese tercero reporto, que es la cifra que hay que confirmar.
    assert "8.130.000" in avisos[0].message


def test_cada_fila_dice_a_quien_le_reportaron():
    """El dato tiene que quedar en la fila, no solo en el aviso: es lo que le permite a
    alguien ver cual valor es el que esta en duda."""
    reading = parse(
        build_exogena_xlsx(detail_rows=[_fila(reported_name="Alejandra Delgado Bautista")])
    )
    assert reading.rows[0].values["reported_name"] == "Alejandra Delgado Bautista"
    assert reading.rows[0].values["reported_id_number"] == "1000000001"
