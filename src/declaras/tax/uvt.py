"""Conversion de UVT a pesos del anio gravable.

La UVT (Unidad de Valor Tributario) cambia cada anio y es la unidad en que la ley expresa
todos los topes y beneficios. La tabla por anio vive en `declaras.parametros` (nunca como
una sola constante, porque en cualquier momento conviven dos: la del anio gravable que se
declara, para calcular sus topes, y la del anio en curso, para sanciones y planeacion);
este modulo la expone con los nombres y los errores de la capa `tax`.

Confundirlas es el error clasico de este dominio: un tope del anio gravable 2025 calculado
con la UVT de 2026 sale inflado y puede llevar a decirle a alguien que no esta obligado a
declarar cuando si lo esta.
"""

from __future__ import annotations

from declaras.domain.errors import ValidationError
from declaras.parametros import UVT_POR_ANIO, uvt_de

# La tabla ya no vive aca: es `parametros.UVT_POR_ANIO`, la unica del proyecto. Este nombre
# se conserva como ALIAS del mismo objeto (no una copia) porque es parte de la interfaz de
# este modulo. Al ser el mismo dict, no puede divergir de la tabla.
UVT_BY_YEAR = UVT_POR_ANIO


def uvt_for(year: int) -> int:
    """Valor de la UVT del anio indicado.

    Delega en `parametros.uvt_de` y traduce su `ValueError` al error de dominio de esta
    capa: quien llama a `tax` atrapa `ValidationError` y espera los `details` (el anio y
    los disponibles) para poder explicar que falto.
    """
    try:
        return uvt_de(year)
    except ValueError as exc:
        raise ValidationError(
            f"No hay UVT registrada para el año {year}.",
            year=year,
            available=sorted(UVT_POR_ANIO),
        ) from exc


def in_pesos(uvt_amount: float, year: int) -> int:
    """Convierte un valor expresado en UVT a pesos del anio indicado.

    NO se redondea. Los topes que publica la DIAN son la multiplicacion exacta: 1.400 UVT
    del anio gravable 2025 son $69.718.600 (1.400 x 49.799), no $69.719.000. Redondear al
    millar mas cercano inflaria el limite en cientos de pesos y podria hacer que alguien
    con ingresos apenas por encima del tope apareciera como no obligado a declarar.
    """
    return int(uvt_amount * uvt_for(year))
