"""La UVT vive en un solo lugar: `parametros.UVT_POR_ANIO`.

Antes de la fusión había dos tablas (una en `tax/uvt.py`, otra implícita en el YAML del año
gravable). Dos tablas del mismo número se separan: una se actualiza con el decreto nuevo y
la otra se queda, y el motor liquida con la UVT del año equivocado sin decir nada.
"""

import pytest

from declaras.parametros import UVT_POR_ANIO, ParametrosAnio, cargar, uvt_de


def test_uvt_por_anio():
    assert uvt_de(2025) == 49_799
    assert uvt_de(2026) == 52_374
    assert uvt_de(2019) == 34_270


def test_anio_sin_uvt_revienta():
    with pytest.raises(ValueError, match="2018"):
        uvt_de(2018)


def test_el_error_dice_que_anios_hay():
    """Sin la lista, el que pide un año que no está no sabe si pedir otro o cargar el decreto."""
    with pytest.raises(ValueError, match="2026"):
        uvt_de(2018)


def test_tax_uvt_delega_en_parametros():
    """tax/uvt.py no puede tener su propia tabla: una sola fuente de verdad.

    El plan pedía `not hasattr(tax_uvt, "UVT_BY_YEAR")`, pero `tests/unit/tax/test_uvt.py`
    importa ese nombre y es contrato de su módulo, que no se toca. Queda como alias del
    MISMO objeto, y eso es lo que se verifica con `is`: un alias no puede divergir de la
    tabla, una copia sí.
    """
    from declaras.tax import uvt as tax_uvt

    assert tax_uvt.uvt_for(2025) == uvt_de(2025)
    assert tax_uvt.UVT_BY_YEAR is UVT_POR_ANIO


def test_el_yaml_del_anio_no_puede_contradecir_la_tabla():
    """El YAML repite la UVT del año; si difiere de la tabla, revienta en vez de liquidar.

    Es el tercer duplicado: `ag2025.yaml` trae `uvt` y `uvt_siguiente` porque el motor los
    lee de ahí. La guarda los ata a la tabla, así que dejan de ser una copia que puede
    quedar vieja.
    """
    datos = cargar(2025).model_dump()
    datos["uvt"] = 49_800
    with pytest.raises(ValueError, match="49799"):
        ParametrosAnio.model_validate(datos)


def test_el_yaml_tampoco_puede_contradecir_la_uvt_del_anio_siguiente():
    """`uvt_siguiente` calcula el anticipo del año que viene: una copia vieja lo subestima."""
    datos = cargar(2025).model_dump()
    datos["uvt_siguiente"] = 52_000
    with pytest.raises(ValueError, match="uvt_siguiente"):
        ParametrosAnio.model_validate(datos)


def test_los_topes_de_obligacion_del_yaml_y_de_tax_no_pueden_divergir():
    """Los topes de "obligado a declarar" siguen escritos dos veces, en UVT.

    `motor/cierre.py` los lee del YAML y `tax/obligation.py` los tiene en su propia tabla:
    son DOS implementaciones de la misma regla legal. Los comparadores ya están unificados
    (`>=` solo en ingresos, `>` en los otros cuatro: arts. 592 num. 1 y 594-3 ET, con sus
    tests de borde en `tests/unit/tax/test_obligation_bordes.py` y `tests/unit/motor/`); lo
    que sigue duplicado son los números, y este test impide que se separen mientras haya
    dos tablas.

    Los tres criterios de flujo del art. 594-3 comparten un único tope en el YAML
    (`tope_obligacion_consignaciones_uvt`, 1.400 UVT): el nombre miente —`cierre.py`
    también compara compras contra él— pero es el número al que los tres están atados.
    """
    from declaras.tax.obligation import THRESHOLD_LIMITS_IN_UVT, ThresholdCode

    p = cargar(2025)
    assert p.tope_obligacion_ingresos_uvt == THRESHOLD_LIMITS_IN_UVT[ThresholdCode.INGRESOS]
    assert p.tope_obligacion_patrimonio_uvt == THRESHOLD_LIMITS_IN_UVT[ThresholdCode.PATRIMONIO]
    for code in (ThresholdCode.MOVIMIENTOS, ThresholdCode.COMPRAS, ThresholdCode.CONSUMO_TARJETA):
        assert p.tope_obligacion_consignaciones_uvt == THRESHOLD_LIMITS_IN_UVT[code], code


def test_un_anio_fuera_de_la_tabla_no_queda_bloqueado():
    """La guarda solo aplica a los años que la tabla conoce.

    Un YAML de un año gravable futuro (con su decreto ya publicado, antes de que alguien
    toque la tabla) tiene que poder cargarse; si no, la guarda impediría declarar.
    """
    datos = cargar(2025).model_dump()
    datos["anio"] = 2030
    datos["uvt"] = 60_000
    datos["uvt_siguiente"] = 63_000
    assert ParametrosAnio.model_validate(datos).uvt == 60_000
