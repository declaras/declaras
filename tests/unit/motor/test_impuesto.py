from declaras.caso import (
    Beneficios,
    CasoTributario,
    Contribuyente,
    Dividendo,
    Donacion,
    Fuente,
)
from declaras.motor.impuesto import impuesto_total
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(dividendos=(), donaciones=()):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="3", nombre="G3"),
        dividendos=list(dividendos),
        beneficios=Beneficios(donaciones_esal=list(donaciones)),
    )


def test_sin_dividendos_solo_tabla():
    t = Traza()
    assert impuesto_total(_caso(), P, t, rlg_general=62_154_472, rlg_pensiones=0) == 1_495_977
    assert t.nodos["DESCUENTO_254_1"].valor == 0


def test_dividendos_mixtos_g3():
    div = Dividendo(
        sociedad_nit="800",
        sociedad_nombre="Soc SA",
        no_gravados=30_000_000,
        gravados=10_000_000,
        fuente=FX,
    )
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=82_478_944, rlg_pensiones=0)
    assert t.nodos["IMP_DIV_35"].valor == 3_500_000
    assert t.nodos["BASE_TABLA_241"].valor == 118_978_944  # 82.478.944 + 30M + 6.5M
    assert t.nodos["IMPUESTO_241"].valor == 15_386_464  # 28% + 116 UVT
    assert t.nodos["DESCUENTO_254_1"].valor == 0  # 36.5M < 1.090 UVT
    assert v == 18_886_464


def test_descuento_254_1_sobre_umbral():
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA", no_gravados=80_000_000, fuente=FX)
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=50_000_000, rlg_pensiones=0)
    # base 130M → imp241 18.472.360 (28% + 116 UVT); descuento 19% × (80M − 54.280.910)
    assert t.nodos["IMPUESTO_241"].valor == 18_472_360
    assert t.nodos["DESCUENTO_254_1"].valor == 4_886_627
    assert v == 13_585_733


def test_imp_div_35_no_pierde_el_peso_de_la_frontera():
    """El 35% de este monto cae exacto en ,50 y debe subir (half-up).

    Multiplicar en float primero da 31.445.438: 0,35 no es exacto en binario y el
    producto aterriza en 31.445.438,499... El camino Decimal da los 31.445.438,50
    reales, que half-up sube. Ver `dinero.porcentaje`.
    """
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA", gravados=89_844_110, fuente=FX)
    t = Traza()
    impuesto_total(_caso([div]), P, t, rlg_general=0, rlg_pensiones=0)
    assert t.nodos["IMP_DIV_35"].valor == 31_445_439


def test_donaciones_certificadas_descuentan_25():
    """Solo la certificada entra: 25% × 4M = 1M (no 25% × 12M)."""
    donaciones = [
        Donacion(entidad="Fundación A", valor=4_000_000, certificada=True, fuente=FX),
        Donacion(entidad="Fundación B", valor=8_000_000, certificada=False, fuente=FX),
    ]
    t = Traza()
    v = impuesto_total(_caso(donaciones=donaciones), P, t, rlg_general=62_154_472, rlg_pensiones=0)
    assert t.nodos["DESCUENTO_DONACIONES"].valor == 1_000_000
    assert v == 495_977  # 1.495.977 − 1.000.000


def test_donacion_no_certificada_no_descuenta():
    donaciones = [Donacion(entidad="Fundación B", valor=8_000_000, certificada=False, fuente=FX)]
    t = Traza()
    v = impuesto_total(_caso(donaciones=donaciones), P, t, rlg_general=62_154_472, rlg_pensiones=0)
    assert t.nodos["DESCUENTO_DONACIONES"].valor == 0
    assert v == 1_495_977


def test_descuentos_mayores_que_el_impuesto_pisan_en_cero():
    """El descuento se registra completo; lo que no baja de 0 es el impuesto neto."""
    donaciones = [Donacion(entidad="Fundación A", valor=20_000_000, certificada=True, fuente=FX)]
    t = Traza()
    v = impuesto_total(_caso(donaciones=donaciones), P, t, rlg_general=62_154_472, rlg_pensiones=0)
    assert t.nodos["DESCUENTO_DONACIONES"].valor == 5_000_000  # 25% × 20M, sin recortar
    assert t.nodos["IMPUESTO_241"].valor == 1_495_977
    assert v == 0  # max(0, 1.495.977 − 5.000.000)
