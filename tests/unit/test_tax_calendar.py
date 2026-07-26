"""El anio gravable se deduce del calendario: es regla, no dato del contribuyente."""

from __future__ import annotations

from datetime import date

from declaras.domain.tax_calendar import default_tax_year, is_supported_tax_year


def test_en_2026_se_declara_el_anio_gravable_2025():
    assert default_tax_year(date(2026, 7, 25)) == 2025


def test_la_regla_se_mantiene_todo_el_anio():
    assert default_tax_year(date(2026, 1, 1)) == 2025
    assert default_tax_year(date(2026, 12, 31)) == 2025


def test_cambia_al_pasar_de_anio():
    assert default_tax_year(date(2027, 1, 1)) == 2026


def test_el_anio_en_curso_no_es_consultable_porque_no_ha_cerrado():
    hoy = date(2026, 7, 25)
    assert is_supported_tax_year(2025, hoy)
    assert not is_supported_tax_year(2026, hoy)


def test_hay_un_limite_inferior_de_soporte():
    hoy = date(2026, 7, 25)
    assert not is_supported_tax_year(2010, hoy)
    assert is_supported_tax_year(2015, hoy)
