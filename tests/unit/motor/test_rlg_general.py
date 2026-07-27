from declaras.caso import (
    Arriendo, Beneficios, CasoTributario, Contribuyente, CostosArriendo,
    Dependiente, Fuente, IngresoLaboral, MontoDeclarado, Rendimiento,
)
from declaras.motor.elecciones import Elecciones
from declaras.motor.general import base_general, rlg_general
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _md(v):
    return MontoDeclarado(valor=v, fuente=FX)


def caso_g1():
    """Asalariado 120M con beneficios: el límite 40% se copa."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="G1"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
            aportes_salud=4_800_000, aportes_pension=4_800_000,
            retencion=8_000_000, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX)],
            medicina_prepagada=_md(6_000_000),
            intereses_vivienda=_md(18_000_000),
            gmf_pagado=_md(1_000_000),
            facturas_electronicas_total=_md(50_000_000),
        ),
    )


def caso_g3_parcial():
    """Asalariado 100M + rendimientos + arriendo: el límite NO se copa, el 387 sí paga."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="3", nombre="G3"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=100_000_000,
            aportes_salud=4_000_000, aportes_pension=4_000_000,
            retencion=6_000_000, fuente=FX)],
        rendimientos=[Rendimiento(entidad="Banco Y", valor=4_000_000,
                                  retencion=280_000, fuente=FX)],
        arriendos=[Arriendo(
            inmueble="Apto", canon_total=36_000_000, retencion=1_260_000,
            costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                                  comision_inmobiliaria=3_600_000), fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX),
                          Dependiente(tipo="hijo_estudiante", fuente=FX)],
            gmf_pagado=_md(900_000),
        ),
    )


def _rlg(caso, e):
    t = Traza()
    base_general(caso, P, t)
    return rlg_general(caso, P, e, t), t


def test_g1_cap_copado_ambas_elecciones_igual():
    v_sin, t = _rlg(caso_g1(), Elecciones(usar_387=False, usar_72uvt=True))
    v_con, _ = _rlg(caso_g1(), Elecciones(usar_387=True, usar_72uvt=True))
    assert v_sin == v_con == 62_154_472
    assert t.nodos["APLICADO_40"].valor == 44_160_000       # cap manda
    assert t.nodos["EXTRA_LIMITE"].valor == 4_085_528       # 72 UVT + 1% facturas


def test_g1_sin_72uvt():
    v, _ = _rlg(caso_g1(), Elecciones(usar_387=False, usar_72uvt=False))
    assert v == 65_740_000  # extra-límite = solo 1% facturas (500.000)


def test_g3_cap_no_copado_387_paga():
    v_con, t = _rlg(caso_g3_parcial(), Elecciones(usar_387=True, usar_72uvt=True))
    v_sin, _ = _rlg(caso_g3_parcial(), Elecciones(usar_387=False, usar_72uvt=True))
    assert v_con == 82_478_944
    assert v_sin == 89_978_944
    assert t.nodos["EXTRA_LIMITE"].valor == 7_171_056  # 72 UVT × 2 dependientes
