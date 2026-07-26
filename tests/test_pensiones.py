from declaras.caso import CasoTributario, Contribuyente, Fuente, IngresoPension
from declaras.motor.pensiones import rlg_pensiones
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(mesadas):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="2", nombre="G2"),
        pensiones=[IngresoPension(pagador="Colpensiones", mesadas=mesadas, fuente=FX)],
    )


def test_mesada_bajo_tope_exenta_total():
    assert rlg_pensiones(_caso([10_000_000] * 12), P, Traza()) == 0


def test_mesada_sobre_tope_grava_exceso_mensual():
    # 55M/mes: exceso (55.000.000 − 49.799.000) × 12 = 62.412.000
    assert rlg_pensiones(_caso([55_000_000] * 12), P, Traza()) == 62_412_000


def test_mesadas_variables_mes_a_mes():
    # solo los meses que exceden 1.000 UVT gravan: 60M excede en 10.201.000
    mesadas = [40_000_000] * 11 + [60_000_000]
    assert rlg_pensiones(_caso(mesadas), P, Traza()) == 10_201_000


def test_sin_pensiones():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="0", nombre="Z"))
    assert rlg_pensiones(caso, P, Traza()) == 0
