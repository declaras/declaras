"""Evaluacion de la obligacion de declarar renta.

Basta cumplir UNO de los cinco topes para quedar obligado. Los valores de referencia los
publica la DIAN en el propio reporte de informacion exogena, asi que el sistema puede
comparar lo que la DIAN dice que uno movio contra el limite legal, y explicar exactamente
por que alguien esta obligado en vez de solo afirmarlo.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from declaras.tax.uvt import in_pesos


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

# Como explicarle cada tope a una persona, sin jerga.
THRESHOLD_LABELS: dict[ThresholdCode, str] = {
    ThresholdCode.INGRESOS: "Ingresos brutos del anio",
    ThresholdCode.PATRIMONIO: "Patrimonio bruto al 31 de diciembre",
    ThresholdCode.CONSUMO_TARJETA: "Consumos con tarjeta de credito",
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


def assess(*, tax_year: int, reported: dict[ThresholdCode, int]) -> ObligationAssessment:
    """Evalua los topes con los valores que la DIAN reporta.

    Un tope sin valor reportado se evalua como cero, no se omite: mostrarle a alguien los
    cinco topes (aunque cuatro esten en cero) es lo que le explica por que esta o no
    obligado, y omitir uno haria parecer que no se reviso.
    """
    from declaras.tax.uvt import uvt_for

    evaluations = [
        ThresholdEvaluation(
            code=code,
            label=THRESHOLD_LABELS[code],
            reported_amount=reported.get(code, 0),
            limit_amount=limit_for(code, tax_year),
            limit_in_uvt=THRESHOLD_LIMITS_IN_UVT[code],
            exceeded=reported.get(code, 0) >= limit_for(code, tax_year),
        )
        for code in ThresholdCode
    ]
    return ObligationAssessment(tax_year=tax_year, uvt=uvt_for(tax_year), thresholds=evaluations)
