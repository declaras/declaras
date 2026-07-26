"""Contrato del motor: 210 esperado por escenario, calculado a mano en el plan."""
from declaras.motor import Elecciones, Liquidacion
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from tests.golden.casos import g0, g1, g2, g3, g4, g5

P = cargar(2025)


def _flags(liq: Liquidacion) -> set[str]:
    """El CONJUNTO EXACTO de flags de la liquidación.

    Se compara por igualdad, no con `tiene_flag`: un flag nuevo que dispare de más
    (un chequeo con el umbral al revés, un aviso que se cuela en el caso limpio) rompe
    el golden en vez de acumularse sin que nadie lo note. Los flags los lee el contador
    en la memoria: uno espurio en cada declaración los vuelve ruido que se ignora.
    """
    return {f.codigo for f in liq.flags}


def test_g0_facil_sin_movimientos():
    liq = optimizar(g0(), P).liquidacion
    assert liq.valor("OBLIGADO_DECLARAR") == 1     # patrimonio > 224.095.500
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.valor("SALDO") == 0
    assert _flags(liq) == set()                    # incremento 0: nada que advertir


def test_g1_asalariado():
    r = optimizar(g1(), P)
    liq = r.liquidacion
    assert r.elecciones == Elecciones(usar_387=False, usar_72uvt=True)
    assert liq.valor("RLG_GENERAL") == 62_154_472
    assert liq.valor("IMPUESTO_NETO") == 1_495_977
    assert liq.valor("RETENCIONES") == 8_000_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == -6_504_023        # a favor
    assert _flags(liq) == set()                    # creció 5M, justificado


def test_g2_pension_y_movimientos():
    liq = optimizar(g2(), P).liquidacion
    assert liq.valor("RLG_GENERAL") == 62_800_000
    assert liq.valor("RLG_PENSIONES") == 62_412_000        # exceso mensual × 12
    assert liq.valor("IMPUESTO_NETO") == 17_131_720        # 28% + 116 UVT
    assert liq.valor("RETENCIONES") == 3_560_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 9_288_790    # 75% − retenciones
    assert liq.valor("SALDO") == 22_860_510
    assert liq.valor("OBLIGADO_DECLARAR") == 1             # también por consignaciones
    assert _flags(liq) == {"COMPONENTE_INFLACIONARIO_PROVISIONAL"}


def test_g3_capital_y_dividendos():
    r = optimizar(g3(), P)
    liq = r.liquidacion
    assert r.elecciones == Elecciones(usar_387=True, usar_72uvt=True)
    assert liq.valor("RLG_GENERAL") == 82_478_944
    assert liq.valor("IMP_DIV_35") == 3_500_000
    assert liq.valor("DESCUENTO_254_1") == 0
    assert liq.valor("IMPUESTO_NETO") == 18_886_464        # 15.386.464 + 3.5M
    assert liq.valor("RETENCIONES") == 7_540_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 6_624_848
    assert liq.valor("SALDO") == 17_971_312
    assert _flags(liq) == {"COMPONENTE_INFLACIONARIO_PROVISIONAL"}


def test_g4_no_obligado():
    liq = optimizar(g4(), P).liquidacion
    assert liq.valor("OBLIGADO_DECLARAR") == 0     # ningún tope superado
    assert _flags(liq) == {"NO_OBLIGADO"}
    assert liq.valor("IMPUESTO_NETO") == 0         # RLG 20.7M < 1.090 UVT
    assert liq.valor("SALDO") == 0


def test_g5_pension_no_uniforme_y_anticipo_promedio():
    liq = optimizar(g5(), P).liquidacion
    # Exención POR MES (1.000 UVT/mesada): solo diciembre grava 160M − 49.799.000.
    # La variante anual (600M − 12.000 UVT) daría 2.412.000 → impuesto 0.
    assert liq.valor("RLG_PENSIONES") == 110_201_000
    assert liq.valor("IMPUESTO_NETO") == 12_928_640    # 28% + 116 UVT
    # Año 2 (1 previo): tasa 50% sobre min(actual, promedio dos años) − retenciones
    # = 50% × 11.464.320 − 1.000.000. Con 75% daría 7.598.240; sin promedio 5.464.320.
    assert liq.valor("ANTICIPO_SIGUIENTE") == 4_732_160
    assert liq.valor("RETENCIONES") == 1_000_000
    # 12.928.640 − 1.000.000 + 4.732.160 − anticipo pagado 2M − saldo favor 500K
    assert liq.valor("SALDO") == 14_160_800
    assert liq.valor("OBLIGADO_DECLARAR") == 1         # ingresos 600M > 1.400 UVT
    assert _flags(liq) == set()
