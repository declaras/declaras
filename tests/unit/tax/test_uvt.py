"""La tabla de UVT y su conversion a pesos.

Estos numeros son la base de todo el calculo tributario: si uno esta mal, todos los topes
y beneficios quedan mal. Las pruebas fijan los valores publicados oficialmente.
"""

from __future__ import annotations

import pytest

from declaras.domain.errors import ValidationError
from declaras.tax.uvt import UVT_BY_YEAR, in_pesos, uvt_for


def test_la_uvt_de_2025_y_2026():
    """Conviven dos UVT: la del anio gravable que se declara y la del anio en curso."""
    assert uvt_for(2025) == 49_799
    assert uvt_for(2026) == 52_374


def test_un_anio_sin_uvt_registrada_falla_con_los_disponibles():
    with pytest.raises(ValidationError) as exc:
        uvt_for(1999)
    assert 2025 in exc.value.details["available"]


@pytest.mark.parametrize(
    ("uvt_amount", "expected_pesos"),
    [
        (1_400, 69_718_600),  # topes de obligacion (ingresos, consumos, movimientos)
        (4_500, 224_095_500),  # tope de patrimonio bruto
        (1_340, 66_730_660),  # limite conjunto de rentas exentas y deducciones
        (790, 39_341_210),  # tope de la renta exenta del 25% laboral
        (72, 3_585_528),  # deduccion por dependiente, por fuera del limite del 40%
        (240, 11_951_760),  # tope del 1% de compras con factura electronica
        (1_200, 59_758_800),  # tope de intereses de vivienda
        (192, 9_561_408),  # tope anual de medicina prepagada
    ],
)
def test_los_topes_oficiales_del_ano_gravable_2025(uvt_amount: float, expected_pesos: int):
    """Valores publicados por la DIAN para el anio gravable 2025."""
    assert in_pesos(uvt_amount, 2025) == expected_pesos


def test_la_conversion_no_redondea():
    """La conversion es la multiplicacion exacta.

    Redondear al millar inflaria el limite: 1.400 UVT quedarian en $69.719.000 y alguien
    con ingresos de $69.718.700 apareceria como no obligado cuando si lo esta.
    """
    assert in_pesos(1_400, 2025) == 1_400 * 49_799
    assert in_pesos(1_400, 2025) != 69_719_000


def test_la_tabla_cubre_los_anios_que_el_conector_puede_consultar():
    """El portal ofrece exogena desde 2020, asi que la tabla no puede quedarse corta."""
    assert set(range(2020, 2027)).issubset(UVT_BY_YEAR)
