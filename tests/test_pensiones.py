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


def _caso_varios(*mesadas_por_pagador):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="2", nombre="G2"),
        pensiones=[
            IngresoPension(pagador=f"Pagador {i}", mesadas=mesadas, fuente=FX)
            for i, mesadas in enumerate(mesadas_por_pagador)
        ],
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


def test_exencion_es_del_contribuyente_no_de_cada_pagador():
    # dos pensiones concurrentes de 30M: el mes agrega 60M y el tope se resta UNA vez
    # → (60.000.000 − 49.799.000) × 12 = 122.412.000 (por pagador habría dado 0)
    caso = _caso_varios([30_000_000] * 12, [30_000_000] * 12)
    assert rlg_pensiones(caso, P, Traza()) == 122_412_000


def test_pagadores_con_mesadas_desiguales():
    # 11 meses de 50M (exceso 201.000 c/u = 2.211.000) + diciembre 50M+10M = 60M
    # (exceso 10.201.000) → 12.412.000
    caso = _caso_varios([50_000_000] * 12, [0] * 11 + [10_000_000])
    assert rlg_pensiones(caso, P, Traza()) == 12_412_000


def test_agregacion_es_mensual_no_anual():
    # Caso discriminante: hay meses BAJO el tope, así que el max(0, ·) muerde y la
    # variante anualizada (Σ mesadas − 12 × tope) ya no coincide.
    #   enero      60M          → 10.201.000
    #   feb-nov    40M (×10)    → 0 (bajo tope)
    #   diciembre  40M + 15M    → 5.201.000
    # correcta 15.402.000 | anualizada 0 | por pagador 10.201.000 | desalineada 25.201.000
    caso = _caso_varios([60_000_000] + [40_000_000] * 11, [0] * 11 + [15_000_000])
    assert rlg_pensiones(caso, P, Traza()) == 15_402_000


def test_sin_pensiones():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="0", nombre="Z"))
    assert rlg_pensiones(caso, P, Traza()) == 0
