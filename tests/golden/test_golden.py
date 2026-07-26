"""Contrato del motor: 210 esperado por escenario, calculado a mano en el plan."""
from declaras.motor import Elecciones
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from tests.golden.casos import g0, g1, g2, g3

P = cargar(2025)


def test_g0_facil_sin_movimientos():
    liq = optimizar(g0(), P).liquidacion
    assert liq.valor("OBLIGADO_DECLARAR") == 1     # patrimonio > 224.095.500
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.valor("SALDO") == 0
    assert not liq.tiene_flag("COMPARACION_PATRIMONIAL")  # incremento 0


def test_g1_asalariado():
    r = optimizar(g1(), P)
    liq = r.liquidacion
    assert r.elecciones == Elecciones(usar_387=False, usar_72uvt=True)
    assert liq.valor("RLG_GENERAL") == 62_154_472
    assert liq.valor("IMPUESTO_NETO") == 1_495_977
    assert liq.valor("RETENCIONES") == 8_000_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == -6_504_023        # a favor
    assert not liq.tiene_flag("COMPARACION_PATRIMONIAL")  # creció 5M, justificado


def test_g2_pension_y_movimientos():
    liq = optimizar(g2(), P).liquidacion
    assert liq.valor("RLG_GENERAL") == 62_800_000
    assert liq.valor("RLG_PENSIONES") == 62_412_000        # exceso mensual × 12
    assert liq.valor("IMPUESTO_NETO") == 17_131_720        # 28% + 116 UVT
    assert liq.valor("RETENCIONES") == 3_560_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 9_288_790    # 75% − retenciones
    assert liq.valor("SALDO") == 22_860_510
    assert liq.valor("OBLIGADO_DECLARAR") == 1             # también por consignaciones
    assert liq.tiene_flag("COMPONENTE_INFLACIONARIO_PROVISIONAL")


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
