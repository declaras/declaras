from declaras.caso import CasoTributario, Contribuyente, Dividendo, Fuente
from declaras.motor.impuesto import impuesto_total
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(dividendos=()):
    return CasoTributario(contribuyente=Contribuyente(num_doc="3", nombre="G3"),
                          dividendos=list(dividendos))


def test_sin_dividendos_solo_tabla():
    t = Traza()
    assert impuesto_total(_caso(), P, t, rlg_general=62_154_472, rlg_pensiones=0) \
        == 1_495_977
    assert t.nodos["DESCUENTO_254_1"].valor == 0


def test_dividendos_mixtos_g3():
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA",
                    no_gravados=30_000_000, gravados=10_000_000, fuente=FX)
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=82_478_944, rlg_pensiones=0)
    assert t.nodos["IMP_DIV_35"].valor == 3_500_000
    assert t.nodos["BASE_TABLA_241"].valor == 118_978_944  # 82.478.944 + 30M + 6.5M
    assert t.nodos["IMPUESTO_241"].valor == 15_386_464     # 28% + 116 UVT
    assert t.nodos["DESCUENTO_254_1"].valor == 0           # 36.5M < 1.090 UVT
    assert v == 18_886_464


def test_descuento_254_1_sobre_umbral():
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA",
                    no_gravados=80_000_000, fuente=FX)
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=50_000_000, rlg_pensiones=0)
    # base 130M → imp241 18.472.360 (28% + 116 UVT); descuento 19% × (80M − 54.280.910)
    assert t.nodos["IMPUESTO_241"].valor == 18_472_360
    assert t.nodos["DESCUENTO_254_1"].valor == 4_886_627
    assert v == 13_585_733
