"""El RUT es un PDF sin campos de formulario: el parser reconstruye el orden por forma,
no por posicion. Cada caso valida que el cursor no se desincronice con campos opcionales."""

from __future__ import annotations

from declaras.documents.models import Confidence
from declaras.documents.parsers.rut import parse
from tests.documents_fixtures import build_rut_pdf


def test_lee_los_catorce_campos_sin_avisos():
    reading = build_and_parse()
    nombres = {f.name for f in reading.fields if f.name != "raw_text"}
    assert nombres == {
        "form_number",
        "nit",
        "verification_digit",
        "collection_office",
        "taxpayer_kind",
        "id_kind",
        "id_number",
        "last_name_1",
        "last_name_2",
        "first_name_1",
        "other_names",
        "email",
        "economic_activity_code",
        "economic_activity_start_date",
    }
    assert reading.warnings == []


def test_el_nit_se_une_sin_espacios_aunque_venga_en_cajas_por_digito():
    reading = build_and_parse(nit="800197268")
    assert reading.field("nit") == "800197268"


def test_el_nombre_completo_se_lee_en_sus_cuatro_partes():
    reading = build_and_parse(
        last_name_1="GARCIA", last_name_2="RUIZ", first_name_1="LUIS", other_names="FERNANDO"
    )
    assert reading.field("last_name_1") == "GARCIA"
    assert reading.field("last_name_2") == "RUIZ"
    assert reading.field("first_name_1") == "LUIS"
    assert reading.field("other_names") == "FERNANDO"


def test_la_fecha_de_actividad_se_normaliza_a_iso():
    reading = build_and_parse(activity_start_date="20220305")
    assert reading.field("economic_activity_start_date") == "2022-03-05"


def test_el_codigo_de_actividad_se_une_sin_espacios():
    reading = build_and_parse(economic_activity_code="0081")
    assert reading.field("economic_activity_code") == "0081"


def test_los_campos_posicionales_son_de_confianza_baja():
    """A diferencia de la exogena (celdas de una hoja), este es un parser posicional
    sobre texto sin estructura: nunca se reporta como cien por ciento cierto."""
    reading = build_and_parse()
    valores = [f for f in reading.fields if f.name != "raw_text"]
    assert all(f.confidence == Confidence.LOW for f in valores)


def test_el_texto_completo_queda_disponible_para_auditoria():
    reading = build_and_parse()
    assert reading.field("raw_text")
    assert "IDENTIFICACIÓN" in reading.field("raw_text")


def test_sin_el_bloque_de_plantilla_se_reporta_el_aviso_correspondiente():
    """Si el portal cambia el PDF y ya no hay plantilla identificable, el parser no debe
    fallar en silencio: debe avisar que la confianza es aun menor de lo usual."""
    from declaras.documents.parsers.rut import parse as parse_rut

    minimal_pdf = _pdf_without_template()
    reading = parse_rut(minimal_pdf)
    assert any(w.code == "TEMPLATE_MARKER_NOT_FOUND" for w in reading.warnings)


def build_and_parse(**overrides):
    return parse(build_rut_pdf(**overrides))


def _pdf_without_template() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.text(10, 10, "un PDF cualquiera sin la plantilla del RUT")
    return bytes(pdf.output())
