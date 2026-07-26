"""Evaluacion de la obligacion de declarar: basta superar un tope."""

from __future__ import annotations

from declaras.tax.obligation import ThresholdCode, assess, limit_for


def test_quien_no_supera_ningun_tope_no_esta_obligado():
    assessment = assess(tax_year=2025, reported={ThresholdCode.INGRESOS: 10_000_000})
    assert not assessment.is_obligated
    assert assessment.exceeded_thresholds == []


def test_basta_superar_un_solo_tope():
    assessment = assess(tax_year=2025, reported={ThresholdCode.INGRESOS: 70_000_000})
    assert assessment.is_obligated
    assert [t.code for t in assessment.exceeded_thresholds] == [ThresholdCode.INGRESOS]


def test_el_tope_se_cumple_al_alcanzarlo_no_solo_al_pasarlo():
    """La ley dice 'iguales o superiores', asi que estar exactamente en el tope obliga."""
    exacto = limit_for(ThresholdCode.INGRESOS, 2025)
    assert assess(tax_year=2025, reported={ThresholdCode.INGRESOS: exacto}).is_obligated
    assert not assess(tax_year=2025, reported={ThresholdCode.INGRESOS: exacto - 1}).is_obligated


def test_siempre_se_evaluan_los_cinco_topes():
    """Mostrar los cinco (aunque cuatro esten en cero) es lo que le explica a alguien por
    que esta o no obligado; omitir uno haria parecer que no se reviso."""
    assessment = assess(tax_year=2025, reported={})
    assert len(assessment.thresholds) == 5
    assert {t.code for t in assessment.thresholds} == set(ThresholdCode)


def test_el_tope_de_patrimonio_es_mucho_mas_alto_que_los_de_flujo():
    """Patrimonio mide acumulacion (4.500 UVT); los otros miden el flujo del anio (1.400)."""
    assert limit_for(ThresholdCode.PATRIMONIO, 2025) == 224_095_500
    assert limit_for(ThresholdCode.INGRESOS, 2025) == 69_718_600


def test_el_margen_dice_cuanto_falta_para_el_tope():
    assessment = assess(tax_year=2025, reported={ThresholdCode.INGRESOS: 69_000_000})
    ingresos = next(t for t in assessment.thresholds if t.code is ThresholdCode.INGRESOS)
    assert ingresos.margin == 69_718_600 - 69_000_000
    assert not ingresos.exceeded


def test_caso_real_dos_topes_superados():
    """Valores reales de una exogena: obligado por ingresos y por movimientos."""
    assessment = assess(
        tax_year=2025,
        reported={
            ThresholdCode.INGRESOS: 70_240_264,
            ThresholdCode.PATRIMONIO: 14_147_658,
            ThresholdCode.CONSUMO_TARJETA: 17_963_118,
            ThresholdCode.MOVIMIENTOS: 99_512_480,
            ThresholdCode.COMPRAS: 3_660_325,
        },
    )
    assert assessment.is_obligated
    assert {t.code for t in assessment.exceeded_thresholds} == {
        ThresholdCode.INGRESOS,
        ThresholdCode.MOVIMIENTOS,
    }


def test_los_topes_cambian_con_el_anio_gravable():
    """La UVT sube cada anio, asi que el mismo ingreso puede obligar un anio y no el otro."""
    assert limit_for(ThresholdCode.INGRESOS, 2024) < limit_for(ThresholdCode.INGRESOS, 2025)
