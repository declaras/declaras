import pytest
from pydantic import BaseModel

from declaras.motor import Elecciones, Flag, Liquidacion, Nodo, liquidar
from declaras.optimizador import ResultadoOptimizacion, ahorro_marginal, optimizar
from declaras.parametros import cargar
from tests.unit.motor.test_rlg_general import caso_g1, caso_g3_parcial

P = cargar(2025)


def test_g1_desempate_prefiere_menos_elecciones():
    r = optimizar(caso_g1(), P)
    # cap copado: (F,T) y (T,T) empatan en impuesto → gana (F,T)
    assert r.elecciones == Elecciones(usar_387=False, usar_72uvt=True)
    assert r.elecciones.activas == 1  # el desempate se decide por este contador
    assert r.liquidacion.valor("IMPUESTO_NETO") == 1_495_977
    assert r.evaluadas == 4


def test_g3_elige_387_y_72uvt():
    r = optimizar(caso_g3_parcial(), P)
    assert r.elecciones == Elecciones(usar_387=True, usar_72uvt=True)
    assert r.elecciones.activas == 2


def test_nunca_peor_que_ingenuo():
    for caso in (caso_g1(), caso_g3_parcial()):
        opt = optimizar(caso, P).liquidacion.valor("IMPUESTO_NETO")
        ingenuo = liquidar(caso, P, Elecciones(usar_387=False,
                                               usar_72uvt=False)).valor("IMPUESTO_NETO")
        assert opt <= ingenuo


def test_ahorro_marginal_de_un_dependiente():
    con = caso_g3_parcial()
    sin = con.model_copy(deep=True)
    sin.beneficios.dependientes = sin.beneficios.dependientes[:1]  # quita 1 de 2
    ahorro = ahorro_marginal(sin, con, P)
    assert ahorro > 0  # un dependiente extra ahorra impuesto real


def test_ahorro_marginal_exige_el_mismo_caso():
    """Restar impuestos de dos contribuyentes distintos no es un ahorro: revienta."""
    with pytest.raises(ValueError, match="MISMO caso"):
        ahorro_marginal(caso_g1(), caso_g3_parcial(), P)


def test_ahorro_marginal_exige_el_mismo_anio():
    con = caso_g3_parcial()
    otro_anio = con.model_copy(deep=True)
    otro_anio.anio_gravable = 2024
    with pytest.raises(ValueError, match="MISMO caso"):
        ahorro_marginal(otro_anio, con, P)


def test_sin_dependientes_un_solo_combo():
    caso = caso_g1()
    caso.beneficios.dependientes = []
    assert optimizar(caso, P).evaluadas == 1


@pytest.mark.parametrize("modelo", [Nodo, Flag, Liquidacion, Elecciones,
                                    ResultadoOptimizacion])
def test_modelos_exportados_prohiben_campos_extra(modelo: type[BaseModel]):
    """Un campo mal escrito debe explotar, no colarse silencioso en la traza."""
    assert modelo.model_config.get("extra") == "forbid"
