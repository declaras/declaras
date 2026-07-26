"""El 210 es un PDF sin campos de formulario y con los numeros de casilla en una imagen de
fondo: el parser ubica cada valor por la posicion en que se dibujo. Lo que estos casos
protegen es justo el modo de falla de un parser posicional, que es poner el valor de una
casilla en el lugar de otra sin que nada lo delate."""

from __future__ import annotations

import pytest

from declaras.documents.models import Confidence
from declaras.documents.parsers.renta_210 import BOX_LABELS, parse
from declaras.domain.errors import DocumentUnreadableError
from tests.documents_fixtures import build_renta_210_pdf


def casillas(reading):
    return {
        int(f.name.removeprefix("casilla_")): f.value
        for f in reading.fields
        if f.name.startswith("casilla_")
    }


def test_lee_el_patrimonio_que_usa_columnas_propias():
    """La franja del patrimonio no comparte las columnas de la cedula general, asi que es la
    primera cosa que se rompe si el mapa de columnas se aplica a ciegas."""
    reading = parse(
        build_renta_210_pdf(
            patrimonio_bruto=350_000_000, deudas=120_000_000, patrimonio_liquido=230_000_000
        )
    )
    valores = casillas(reading)
    assert valores[29] == 350_000_000
    assert valores[30] == 120_000_000
    assert valores[31] == 230_000_000


def test_lee_las_cuatro_columnas_de_la_cedula_general():
    """El mismo concepto se repite en cuatro columnas y solo la posicion las distingue: la
    columna de rentas de trabajo y la de rentas no laborales no se pueden confundir."""
    reading = parse(
        build_renta_210_pdf(ingresos_brutos=90_000_000, ingresos_no_laborales=4_000_000)
    )
    valores = casillas(reading)
    assert valores[32] == 90_000_000  # rentas de trabajo
    assert valores[43] == 0  # honorarios
    assert valores[58] == 0  # rentas de capital
    assert valores[74] == 4_000_000  # rentas no laborales


def test_lee_una_fila_que_el_formulario_solo_abre_en_la_ultima_columna():
    """Las devoluciones y descuentos solo existen para rentas no laborales. Si el parser
    asumiera que toda fila empieza en la primera columna, ese valor caeria en la casilla 32."""
    valores = casillas(parse(build_renta_210_pdf()))
    assert valores[75] == 0
    assert valores[32] != 0


def test_lee_la_franja_de_totales_con_su_propia_geometria():
    reading = parse(build_renta_210_pdf(renta_liquida=74_000_000, ingresos_no_laborales=5_000_000))
    valores = casillas(reading)
    assert valores[91] == 79_000_000  # renta liquida de la cedula general
    assert valores[93] == 79_000_000  # renta liquida ordinaria de la cedula general


def test_lee_el_anio_gravable_y_la_cedula_digito_por_digito():
    """Ambos se imprimen en casillas individuales, un digito en cada una."""
    reading = parse(build_renta_210_pdf(tax_year=2023, id_number=1020304050))
    assert reading.field("tax_year") == 2023
    assert reading.field("id_number") == 1020304050


def test_el_digito_de_verificacion_no_se_pega_a_la_cedula():
    """Va en la misma linea, separado por un espacio mayor. Pegarlo daria una cedula de once
    digitos y el cruce de identidad con el RUT fallaria siempre."""
    reading = parse(build_renta_210_pdf(id_number=1020304050, verification_digit=9))
    assert reading.field("id_number") == 1020304050


def test_avisa_cuando_las_identidades_del_formulario_no_cuadran():
    """El patrimonio liquido tiene que ser el bruto menos las deudas. Si no lo es, el mapa se
    desincronizo y hay que avisar en vez de entregar numeros equivocados en silencio."""
    reading = parse(
        build_renta_210_pdf(
            patrimonio_bruto=50_000_000, deudas=20_000_000, patrimonio_liquido=99_000_000
        )
    )
    codigos = {w.code for w in reading.warnings}
    assert "FORM_ARITHMETIC_MISMATCH" in codigos
    aviso = next(w for w in reading.warnings if w.code == "FORM_ARITHMETIC_MISMATCH")
    assert "31" in aviso.message


def test_tolera_el_redondeo_al_millar_del_formulario():
    """El formulario redondea, asi que una diferencia de menos de mil pesos no es un error."""
    reading = parse(
        build_renta_210_pdf(
            patrimonio_bruto=50_000_000, deudas=20_000_000, patrimonio_liquido=30_000_400
        )
    )
    assert [w for w in reading.warnings if w.code == "FORM_ARITHMETIC_MISMATCH"] == []


def test_avisa_cuando_no_reconoce_la_disposicion_del_formulario():
    """Otra version del 210 movería las filas: mejor no leer nada y decirlo."""
    from tests.documents_fixtures import _build_pdf_with_positioned_text

    otro_formulario = _build_pdf_with_positioned_text([(300.0, 200.0, "12,345,000")])
    reading = parse(otro_formulario)
    assert casillas(reading) == {}
    assert {w.code for w in reading.warnings} == {"FORM_LAYOUT_NOT_RECOGNIZED"}


def test_avisa_cuando_aparece_un_valor_en_una_celda_que_el_formulario_deja_en_blanco():
    """El formulario sombrea las celdas que no aplican. Un valor ahi significa que el mapa no
    corresponde a esta version, y es la senal mas temprana de que la DIAN cambio el layout."""
    from tests.documents_fixtures import _build_pdf_with_positioned_text

    # Fila de devoluciones y descuentos, que solo existe en la ultima columna, con un valor
    # puesto en la primera.
    reading = parse(_build_pdf_with_positioned_text([(190.0, 557.5, "1,000,000")]))
    assert {w.code for w in reading.warnings} == {"FORM_LAYOUT_NOT_RECOGNIZED"}


def test_falla_claro_si_el_archivo_no_es_un_pdf():
    with pytest.raises(DocumentUnreadableError):
        parse(b"esto no es un pdf")


def test_cada_casilla_se_explica_con_el_nombre_impreso_en_el_formulario():
    """Ningun consumidor deberia tener que saber que es "la casilla 42": ni la interfaz del
    cliente, ni el contador, ni el agente."""
    reading = parse(build_renta_210_pdf())
    for field in reading.fields:
        if field.name.startswith("casilla_"):
            assert field.source in BOX_LABELS.values()
            assert not field.source.startswith("casilla")


def test_los_montos_se_marcan_con_confianza_baja_y_en_pesos():
    """Son una lectura posicional, no un dato estructurado: quien los consuma debe saber que
    conviene confirmarlos."""
    reading = parse(build_renta_210_pdf())
    montos = [f for f in reading.fields if f.name.startswith("casilla_")]
    assert montos
    assert all(f.confidence is Confidence.LOW for f in montos)
    assert all(f.unit == "COP" for f in montos)
