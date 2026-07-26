"""Golden cases sintéticos, verificables a mano. Un caso por escenario del spec."""
from declaras.caso import (
    Activo, Arriendo, Beneficios, CasoTributario, Contribuyente, CostosArriendo,
    Creditos, Dependiente, Deuda, Dividendo, Fuente, IngresoLaboral,
    IngresoPension, MontoDeclarado, Movimientos, Patrimonio, Rendimiento,
)

FX = Fuente.fixture("golden")


def _md(v: int) -> MontoDeclarado:
    return MontoDeclarado(valor=v, fuente=FX)


def g0() -> CasoTributario:
    """Fácil sin movimientos: obligado solo por patrimonio, impuesto 0."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="10", nombre="G0 Fácil"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT",
                            valor_31dic=250_000_000, fuente=FX)],
            patrimonio_liquido_anterior=250_000_000),
    )


def g1() -> CasoTributario:
    """Asalariado con beneficios: el límite del 40% se copa."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="11", nombre="G1 Asalariado"),
        laborales=[IngresoLaboral(
            empleador_nit="900111222", empleador_nombre="ACME SAS",
            salarios=120_000_000, aportes_salud=4_800_000,
            aportes_pension=4_800_000, retencion=8_000_000, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX)],
            medicina_prepagada=_md(6_000_000),
            intereses_vivienda=_md(18_000_000),
            gmf_pagado=_md(1_000_000),
            facturas_electronicas_total=_md(50_000_000)),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="inmueble", descripcion="Apto",
                            valor_31dic=300_000_000, fuente=FX),
                     Activo(tipo="cuenta", descripcion="Ahorros",
                            valor_31dic=20_000_000, fuente=FX)],
            deudas=[Deuda(acreedor="Banco", saldo_31dic=150_000_000, fuente=FX)],
            patrimonio_liquido_anterior=165_000_000),
        creditos=Creditos(anios_previos_declarando=0),
    )


def g2() -> CasoTributario:
    """Asalariado + pensión alta + movimientos (rendimientos, GMF, consignaciones)."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="12", nombre="G2 Pensionado"),
        laborales=[IngresoLaboral(
            empleador_nit="900333444", empleador_nombre="Universidad X",
            salarios=80_000_000, aportes_salud=3_200_000,
            aportes_pension=3_200_000, retencion=3_000_000, fuente=FX)],
        pensiones=[IngresoPension(pagador="Colpensiones",
                                  mesadas=[55_000_000] * 12, fuente=FX)],
        rendimientos=[Rendimiento(entidad="Banco Y", valor=8_000_000,
                                  retencion=560_000, fuente=FX)],
        beneficios=Beneficios(gmf_pagado=_md(800_000)),
        movimientos=Movimientos(consignaciones_totales=_md(700_000_000)),
        creditos=Creditos(anios_previos_declarando=2),
    )


def g3() -> CasoTributario:
    """Asalariado + rendimientos + arriendos con costos + dividendos mixtos."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="13", nombre="G3 Capital"),
        laborales=[IngresoLaboral(
            empleador_nit="900555666", empleador_nombre="Consultora Z",
            salarios=100_000_000, aportes_salud=4_000_000,
            aportes_pension=4_000_000, retencion=6_000_000, fuente=FX)],
        rendimientos=[Rendimiento(entidad="Banco Y", valor=4_000_000,
                                  retencion=280_000, fuente=FX)],
        arriendos=[Arriendo(
            inmueble="Apto arrendado", canon_total=36_000_000, retencion=1_260_000,
            costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                                  comision_inmobiliaria=3_600_000), fuente=FX)],
        dividendos=[Dividendo(sociedad_nit="800777888", sociedad_nombre="Soc SA",
                              no_gravados=30_000_000, gravados=10_000_000,
                              retencion=0, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX),
                          Dependiente(tipo="hijo_estudiante", fuente=FX)],
            gmf_pagado=_md(900_000)),
        creditos=Creditos(anios_previos_declarando=2),
    )
