"""Tabla de UVT por anio gravable.

La UVT (Unidad de Valor Tributario) cambia cada anio y es la unidad en que la ley expresa
todos los topes y beneficios. Se codifica como tabla por anio y nunca como una sola
constante, porque en cualquier momento conviven dos: la del anio gravable que se declara
(para calcular sus topes) y la del anio en curso (para sanciones y planeacion).

Confundirlas es el error clasico de este dominio: un tope del anio gravable 2025 calculado
con la UVT de 2026 sale inflado y puede llevar a decirle a alguien que no esta obligado a
declarar cuando si lo esta.
"""

from __future__ import annotations

from declaras.domain.errors import ValidationError

# Valor de la UVT por anio gravable, en pesos.
UVT_BY_YEAR: dict[int, int] = {
    2019: 34_270,
    2020: 35_607,
    2021: 36_308,
    2022: 38_004,
    2023: 42_412,
    2024: 47_065,
    2025: 49_799,
    2026: 52_374,
}


def uvt_for(year: int) -> int:
    """Valor de la UVT del anio indicado."""
    value = UVT_BY_YEAR.get(year)
    if value is None:
        raise ValidationError(
            f"No hay UVT registrada para el año {year}.",
            year=year,
            available=sorted(UVT_BY_YEAR),
        )
    return value


def in_pesos(uvt_amount: float, year: int) -> int:
    """Convierte un valor expresado en UVT a pesos del anio indicado.

    NO se redondea. Los topes que publica la DIAN son la multiplicacion exacta: 1.400 UVT
    del anio gravable 2025 son $69.718.600 (1.400 x 49.799), no $69.719.000. Redondear al
    millar mas cercano inflaria el limite en cientos de pesos y podria hacer que alguien
    con ingresos apenas por encima del tope apareciera como no obligado a declarar.
    """
    return int(uvt_amount * uvt_for(year))
