"""Comparar lecturas y no archivos es lo que permite decir "no hubo cambios" con certeza.

La DIAN incrusta la fecha de generacion en cada descarga, asi que el mismo documento bajado
dos veces es un archivo distinto. Estos casos fijan que se considera un cambio y que no.
"""

from __future__ import annotations

from declaras.documents.models import Confidence, DocumentReading, ExtractedField, ExtractedRow
from declaras.services.reading_diff import compare, describe_sync


def _lectura(**valores) -> DocumentReading:
    campos = [
        ExtractedField(name=n, value=v, confidence=Confidence.DETERMINISTIC, source=n)
        for n, v in valores.items()
    ]
    return DocumentReading(doc_type="EXOGENA", parser="p", content_sha256="x" * 64, fields=campos)


def test_sin_lectura_anterior_el_documento_es_nuevo():
    diff = compare(doc_type="EXOGENA", before=None, after=_lectura(tope_ingresos=1))
    assert diff.is_new
    assert diff.has_changes


def test_dos_lecturas_iguales_no_son_un_cambio():
    diff = compare(
        doc_type="EXOGENA", before=_lectura(tope_ingresos=1), after=_lectura(tope_ingresos=1)
    )
    assert not diff.has_changes
    assert diff.changed_fields == []


def test_la_fecha_de_generacion_del_reporte_no_cuenta_como_cambio():
    """Cambia en cada descarga sin que cambie ni un peso de lo reportado."""
    diff = compare(
        doc_type="EXOGENA",
        before=_lectura(tope_ingresos=1, report_date="2026-07-01", cutoff_date="2026-06-30"),
        after=_lectura(tope_ingresos=1, report_date="2026-07-26", cutoff_date="2026-07-24"),
    )
    assert not diff.has_changes


def test_un_valor_distinto_si_es_un_cambio_y_dice_de_cuanto_a_cuanto():
    diff = compare(
        doc_type="EXOGENA",
        before=_lectura(tope_ingresos=70_000_000),
        after=_lectura(tope_ingresos=99_000_000),
    )
    assert diff.has_changes
    cambio = diff.changed_fields[0]
    assert (cambio.before, cambio.after) == (70_000_000, 99_000_000)


def test_un_tercero_nuevo_que_reporto_es_un_cambio_aunque_los_topes_no_se_muevan():
    """Un tercero puede reportar algo que no mueve ningun tope pero si cambia los renglones
    sugeridos, y eso hay que revisarlo."""
    antes = _lectura(tope_ingresos=1)
    despues = _lectura(tope_ingresos=1).model_copy(
        update={"rows": [ExtractedRow(source="fila 20", values={"amount": 5})]}
    )
    diff = compare(doc_type="EXOGENA", before=antes, after=despues)
    assert diff.has_changes
    assert diff.rows_changed


def test_un_documento_que_no_se_pudo_leer_no_se_anuncia_como_actualizado():
    """No se puede afirmar que algo cambio si no se pudo verificar."""
    diff = compare(doc_type="PRIOR_RETURN", before=_lectura(casilla_29=1), after=None)
    assert not diff.has_changes


# ─────── como se le cuenta a una persona ───────


def test_sin_cambios_se_dice_sin_cambios():
    diffs = [
        compare(doc_type=t, before=_lectura(a=1), after=_lectura(a=1)) for t in ("EXOGENA", "RUT")
    ]
    assert "no encontró cambios" in describe_sync(diffs)


def test_la_primera_consulta_no_dice_que_algo_cambio():
    diffs = [compare(doc_type=t, before=None, after=_lectura(a=1)) for t in ("EXOGENA", "RUT")]
    mensaje = describe_sync(diffs)
    assert "trajo 2 documentos" in mensaje
    assert "cambió" not in mensaje


def test_cuando_cambia_uno_de_varios_se_nombra_ese_y_se_dice_que_el_resto_sigue_igual():
    diffs = [
        compare(doc_type="EXOGENA", before=_lectura(a=1), after=_lectura(a=2)),
        compare(doc_type="RUT", before=_lectura(a=1), after=_lectura(a=1)),
    ]
    mensaje = describe_sync(diffs)
    assert "exógena" in mensaje.lower()
    assert "el resto sigue igual" in mensaje
    assert "RUT" not in mensaje.replace("exógena", "")


def test_una_consulta_sin_documentos_no_miente():
    assert "no trajo documentos" in describe_sync([])
