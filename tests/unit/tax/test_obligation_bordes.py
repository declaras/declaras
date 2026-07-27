"""El borde exacto de los cuatro topes que exigen PASAR el limite.

`test_obligation.py` fija el borde de INGRESOS, el unico tope "iguales o superiores":
el art. 592 num. 1 ET define al no obligado por ingresos brutos "inferiores a 1.400
UVT", asi que llegar al tope ya obliga. Los otros cuatro no: patrimonio por el mismo
numeral ("patrimonio bruto que no exceda de 4.500 UVT") y los tres de flujo por el
art. 594-3 ET ("que no excedan", "que no superen", "que no exceda").

Sin estos casos se podia voltear el comparador de los cuatro (aca o en el `motor/cierre.py`
que evalua la misma regla) y la suite seguia verde: asi aparecio la divergencia en la que
un patrimonio de exactamente 4.500 UVT obligaba segun el resumen del caso y no segun la
liquidacion.
"""

from __future__ import annotations

import pytest

from declaras.tax.obligation import ThresholdCode, ThresholdEvaluation, assess, limit_for

# Los cuatro estrictos. INGRESOS queda fuera a proposito: su borde es el del test de
# `test_obligation.py`, y ahi `>=` es lo correcto.
TOPES_ESTRICTOS = [
    ThresholdCode.PATRIMONIO,
    ThresholdCode.CONSUMO_TARJETA,
    ThresholdCode.MOVIMIENTOS,
    ThresholdCode.COMPRAS,
]


def _evaluar(code: ThresholdCode, reportado: int) -> tuple[bool, ThresholdEvaluation]:
    """Evalua un solo tope: los otros cuatro quedan en cero y no interfieren."""
    assessment = assess(tax_year=2025, reported={code: reportado})
    return assessment.is_obligated, next(t for t in assessment.thresholds if t.code is code)


@pytest.mark.parametrize("code", TOPES_ESTRICTOS)
def test_quedar_exactamente_en_un_tope_estricto_no_obliga(code):
    """El verbo de la norma es estricto ("no exceda", "no superen"): estar en el tope
    no lo excede."""
    obligado, evaluacion = _evaluar(code, limit_for(code, 2025))
    assert not evaluacion.exceeded
    assert not obligado
    assert evaluacion.margin == 0  # exactamente en el limite, ni un peso de margen


@pytest.mark.parametrize("code", TOPES_ESTRICTOS)
def test_un_peso_por_encima_de_un_tope_estricto_ya_obliga(code):
    """El tope no es un rango de tolerancia: el peso 1 por encima ya excede."""
    obligado, evaluacion = _evaluar(code, limit_for(code, 2025) + 1)
    assert evaluacion.exceeded
    assert obligado
    assert evaluacion.margin == -1
