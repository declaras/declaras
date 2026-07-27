"""Evaluacion de la obligacion de declarar renta.

Basta cumplir UNO de los cinco topes para quedar obligado. Los valores de referencia los
publica la DIAN en el propio reporte de informacion exogena, asi que el sistema puede
comparar lo que la DIAN dice que uno movio contra el limite legal, y explicar exactamente
por que alguien esta obligado en vez de solo afirmarlo.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from declaras.tax.uvt import in_pesos, uvt_for


class ThresholdCode(StrEnum):
    """Los cinco criterios del articulo 592 del Estatuto Tributario y su reglamento."""

    INGRESOS = "ingresos"
    PATRIMONIO = "patrimonio"
    CONSUMO_TARJETA = "consumo_tarjeta"
    MOVIMIENTOS = "movimientos"
    COMPRAS = "compras"


# Limite legal de cada tope, en UVT. Cuatro de los cinco comparten el mismo limite; el de
# patrimonio es mucho mas alto porque mide acumulacion, no flujo del anio.
THRESHOLD_LIMITS_IN_UVT: dict[ThresholdCode, float] = {
    ThresholdCode.INGRESOS: 1_400,
    ThresholdCode.PATRIMONIO: 4_500,
    ThresholdCode.CONSUMO_TARJETA: 1_400,
    ThresholdCode.MOVIMIENTOS: 1_400,
    ThresholdCode.COMPRAS: 1_400,
}

# Topes cuyo comparador es "iguales o superiores" (`>=`). La norma es asimetrica a
# proposito y el comparador sale del verbo de cada articulo, uno por uno:
#
# - INGRESOS: el art. 592 num. 1 ET define al NO obligado como quien tuvo ingresos brutos
#   "inferiores a 1.400 UVT". Negado, obligado es quien llega a 1.400 UVT, asi que estar
#   exactamente en el tope ya obliga -> `>=`.
# - PATRIMONIO: mismo numeral del art. 592, pero el verbo cambia: patrimonio bruto que "no
#   exceda de 4.500 UVT". Estar exactamente en 4.500 UVT no lo excede -> `>` estricto.
# - CONSUMO_TARJETA, MOVIMIENTOS y COMPRAS: art. 594-3 ET, los tres con verbo estricto
#   ("que no excedan", "que no superen", "que no exceda") -> `>` estricto, igual que
#   patrimonio.
#
# El motor (`motor/cierre.py`) compara con estos mismos comparadores: son dos
# implementaciones de la misma regla y en el borde exacto tienen que responder lo mismo,
# porque una alimenta el resumen del caso y la otra la liquidacion.
THRESHOLDS_INCLUSIVE_OF_LIMIT: frozenset[ThresholdCode] = frozenset({ThresholdCode.INGRESOS})

# Como explicarle cada tope a una persona, sin jerga. Es texto que ve el usuario, asi que
# va con la ortografia correcta (los comentarios y nombres del codigo van sin tildes, pero
# el contenido que se muestra, no).
THRESHOLD_LABELS: dict[ThresholdCode, str] = {
    ThresholdCode.INGRESOS: "Ingresos brutos del año",
    ThresholdCode.PATRIMONIO: "Patrimonio bruto al 31 de diciembre",
    ThresholdCode.CONSUMO_TARJETA: "Consumos con tarjeta de crédito",
    ThresholdCode.MOVIMIENTOS: "Consignaciones y movimientos en cuentas",
    ThresholdCode.COMPRAS: "Compras y consumos totales",
}


class ThresholdEvaluation(BaseModel):
    """Un tope evaluado: cuanto reporta la DIAN, cual es el limite y si se supera."""

    code: ThresholdCode
    label: str
    reported_amount: int
    limit_amount: int
    limit_in_uvt: float
    exceeded: bool

    @property
    def margin(self) -> int:
        """Cuanto falta para el limite (negativo si ya se supero)."""
        return self.limit_amount - self.reported_amount


class ObligationAssessment(BaseModel):
    """Resultado de evaluar los cinco topes de un anio gravable."""

    tax_year: int
    uvt: int
    thresholds: list[ThresholdEvaluation] = Field(default_factory=list)

    @property
    def is_obligated(self) -> bool:
        """Basta con superar un solo tope."""
        return any(t.exceeded for t in self.thresholds)

    @property
    def exceeded_thresholds(self) -> list[ThresholdEvaluation]:
        return [t for t in self.thresholds if t.exceeded]


def limit_for(code: ThresholdCode, year: int) -> int:
    """Limite legal de un tope, en pesos del anio gravable."""
    return in_pesos(THRESHOLD_LIMITS_IN_UVT[code], year)


def exceeds_limit(code: ThresholdCode, reported_amount: int, limit_amount: int) -> bool:
    """Si el valor reportado supera el tope, con el comparador que ese tope exige.

    Ver `THRESHOLDS_INCLUSIVE_OF_LIMIT`: solo ingresos obliga al alcanzar el tope; los
    otros cuatro exigen pasarlo.
    """
    if code in THRESHOLDS_INCLUSIVE_OF_LIMIT:
        return reported_amount >= limit_amount
    return reported_amount > limit_amount


def assess(*, tax_year: int, reported: dict[ThresholdCode, int]) -> ObligationAssessment:
    """Evalua los topes con los valores que la DIAN reporta.

    Un tope sin valor reportado se evalua como cero, no se omite: mostrarle a alguien los
    cinco topes (aunque cuatro esten en cero) es lo que le explica por que esta o no
    obligado, y omitir uno haria parecer que no se reviso.
    """
    evaluations = [_evaluate(code, reported.get(code, 0), tax_year) for code in ThresholdCode]
    return ObligationAssessment(tax_year=tax_year, uvt=uvt_for(tax_year), thresholds=evaluations)


def _evaluate(code: ThresholdCode, reported_amount: int, year: int) -> ThresholdEvaluation:
    limit_amount = limit_for(code, year)
    return ThresholdEvaluation(
        code=code,
        label=THRESHOLD_LABELS[code],
        reported_amount=reported_amount,
        limit_amount=limit_amount,
        limit_in_uvt=THRESHOLD_LIMITS_IN_UVT[code],
        exceeded=exceeds_limit(code, reported_amount, limit_amount),
    )
