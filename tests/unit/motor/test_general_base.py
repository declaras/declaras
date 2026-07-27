from declaras.caso import (
    Arriendo, CasoTributario, Contribuyente, CostosArriendo, Fuente,
    IngresoLaboral, Rendimiento,
)
from declaras.motor.general import base_general
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(**kw):
    base = dict(contribuyente=Contribuyente(num_doc="1", nombre="X"))
    base.update(kw)
    return CasoTributario(**base)


LABORAL = IngresoLaboral(
    empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
    aportes_salud=4_800_000, aportes_pension=4_800_000, retencion=8_000_000, fuente=FX,
)


def test_solo_laboral():
    t = Traza()
    base_general(_caso(laborales=[LABORAL]), P, t)
    assert t.nodos["ING_BRUTO_GENERAL"].valor == 120_000_000
    assert t.nodos["INCR_TOTAL"].valor == 9_600_000
    assert t.nodos["ING_NETOS_GENERAL"].valor == 110_400_000
    assert t.nodos["CAP_40"].valor == 44_160_000  # 40% < 1.340 UVT


def test_cap_40_tope_1340_uvt():
    """Ingresos netos > 166.826.650: manda el tope de 1.340 UVT, no el 40%."""
    t = Traza()
    alto = IngresoLaboral(
        empleador_nit="900", empleador_nombre="ACME", salarios=200_000_000,
        aportes_salud=8_000_000, aportes_pension=8_000_000, fuente=FX,
    )
    base_general(_caso(laborales=[alto]), P, t)
    assert t.nodos["ING_NETOS_GENERAL"].valor == 184_000_000  # 40% = 73.600.000 > tope
    assert t.nodos["CAP_40"].valor == 66_730_660  # 1.340 UVT × 49.799


def test_rendimientos_con_ci_provisional():
    t = Traza()
    caso = _caso(rendimientos=[Rendimiento(entidad="Banco", valor=8_000_000,
                                           retencion=560_000, fuente=FX)])
    base_general(caso, P, t)
    assert t.nodos["INCR_CI"].valor == 0
    assert any(f.codigo == "COMPONENTE_INFLACIONARIO_PROVISIONAL" for f in t.flags)


def test_arriendos_restan_costos():
    t = Traza()
    caso = _caso(arriendos=[Arriendo(
        inmueble="Apto 101", canon_total=36_000_000, retencion=1_260_000,
        costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                              comision_inmobiliaria=3_600_000), fuente=FX)])
    base_general(caso, P, t)
    assert t.nodos["ING_BRUTO_GENERAL"].valor == 36_000_000
    assert t.nodos["COSTOS_ARRIENDOS"].valor == 11_400_000
