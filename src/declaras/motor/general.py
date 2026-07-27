from declaras.caso import CasoTributario
from declaras.dinero import porcentaje
from declaras.motor.elecciones import Elecciones
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def base_general(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> None:
    """Pasos 1-2 del art. 336: ingresos brutos, INCRNGO y base del límite 40%."""
    bruto_laboral = sum(l.bruto for l in caso.laborales)
    total_rend = sum(r.valor for r in caso.rendimientos)
    total_arriendos = sum(a.canon_total for a in caso.arriendos)

    bruto = t.nodo(
        "ING_BRUTO_GENERAL", "Ingresos brutos cédula general",
        bruto_laboral + total_rend + total_arriendos,
        f"laborales {bruto_laboral:,} + rendimientos {total_rend:,} + arriendos {total_arriendos:,}",
        regla="art. 335 ET",
    )

    aportes = t.nodo(
        "INCR_APORTES", "INCRNGO aportes obligatorios salud/pensión",
        sum(l.aportes_salud + l.aportes_pension for l in caso.laborales),
        "suma aportes obligatorios de cada 220",
        regla="arts. 55-56 ET",
    )

    if total_rend and p.componente_inflacionario is None:
        t.flag(
            "COMPONENTE_INFLACIONARIO_PROVISIONAL",
            f"El decreto del componente inflacionario AG {p.anio} no ha salido: "
            f"se usa 0% (conservador). Actualizar ag{p.anio}.yaml cuando se expida.",
        )
    pct_ci = p.componente_inflacionario or 0.0
    ci = t.nodo(
        "INCR_CI", "INCRNGO componente inflacionario de rendimientos",
        porcentaje(total_rend, pct_ci),
        f"{pct_ci:.2%} × rendimientos {total_rend:,}",
        regla="arts. 38-41 ET",
    )

    incr = t.nodo("INCR_TOTAL", "Total INCRNGO", aportes + ci,
                  "INCR_APORTES + INCR_CI", insumos=["INCR_APORTES", "INCR_CI"])

    netos = t.nodo("ING_NETOS_GENERAL", "Ingresos netos (base del límite 40%)",
                   bruto - incr, "ING_BRUTO_GENERAL − INCR_TOTAL",
                   insumos=["ING_BRUTO_GENERAL", "INCR_TOTAL"], regla="art. 336 num. 3")

    t.nodo("COSTOS_ARRIENDOS", "Costos y gastos procedentes de arriendos",
           sum(a.costos.total for a in caso.arriendos),
           "predial + administración + comisión + reparaciones (con soporte)",
           regla="art. 336 num. 4")

    t.nodo("CAP_40",
           f"Límite exentas+deducciones (menor entre {p.limite_general_pct:.0%} "
           f"y {p.limite_general_uvt:,} UVT)",
           min(porcentaje(netos, p.limite_general_pct),
               p.uvt_pesos(p.limite_general_uvt)),
           f"min({p.limite_general_pct:.0%} × {netos:,}, {p.limite_general_uvt:,} UVT "
           f"= {p.uvt_pesos(p.limite_general_uvt):,})",
           insumos=["ING_NETOS_GENERAL"], regla="art. 336 num. 3")


def rlg_general(caso: CasoTributario, p: ParametrosAnio, e: Elecciones, t: Traza) -> int:
    """Pasos 3-5 del art. 336: deducciones/exentas con límite, extra-límite y RLG."""
    bruto_laboral = sum(l.bruto for l in caso.laborales)
    incr_aportes = t.nodos["INCR_APORTES"].valor
    b = caso.beneficios

    intereses = min(b.intereses_vivienda.valor if b.intereses_vivienda else 0,
                    p.uvt_pesos(p.intereses_vivienda_tope_uvt))
    prepagada = min(b.medicina_prepagada.valor if b.medicina_prepagada else 0,
                    p.uvt_pesos(p.prepagada_tope_uvt_anio))
    icetex = min(b.intereses_icetex.valor if b.intereses_icetex else 0,
                 p.uvt_pesos(p.icetex_tope_uvt))
    gmf = porcentaje(b.gmf_pagado.valor if b.gmf_pagado else 0, p.gmf_pct_deducible)
    afc = min(sum(a.valor for a in b.aportes_afc_fvp),
              porcentaje(t.nodos["ING_BRUTO_GENERAL"].valor, p.afc_pct),
              p.uvt_pesos(p.afc_tope_uvt))
    ded_387 = 0
    if e.usar_387 and b.dependientes:
        ded_387 = min(porcentaje(bruto_laboral, p.ded_387_pct),
                      p.uvt_pesos(p.ded_387_tope_uvt_mes * 12))

    deducciones = t.nodo(
        "DEDUCCIONES_LIMITADAS", "Deducciones dentro del límite 40%",
        intereses + prepagada + icetex + gmf + afc + ded_387,
        f"vivienda {intereses:,} + prepagada {prepagada:,} + icetex {icetex:,} "
        f"+ GMF50% {gmf:,} + AFC/FVP {afc:,} + art387 {ded_387:,}",
        regla="arts. 119, 126-1, 126-4, 115, 387 ET",
    )

    # Interpretación I-1: base del 25% excluye GMF, 72 UVT y 1% (validar con contador).
    base_25 = max(0, bruto_laboral - incr_aportes
                  - (intereses + prepagada + icetex + ded_387 + afc))
    exenta_25 = t.nodo(
        "EXENTA_25", "Renta exenta 25% laboral (tope 790 UVT)",
        min(porcentaje(base_25, p.exenta_laboral_pct),
            p.uvt_pesos(p.exenta_laboral_tope_uvt)),
        f"min(25% × base {base_25:,}, 790 UVT)", regla="art. 206 num. 10 ET",
    )

    cap = t.nodos["CAP_40"].valor
    aplicado = t.nodo(
        "APLICADO_40", "Exentas + deducciones aplicadas (tras el límite)",
        min(deducciones + exenta_25, cap),
        f"min({deducciones:,} + {exenta_25:,}, cap {cap:,})",
        insumos=["DEDUCCIONES_LIMITADAS", "EXENTA_25", "CAP_40"],
        regla="art. 336 num. 3",
    )

    dep_72 = 0
    if e.usar_72uvt and b.dependientes:
        n = min(len(b.dependientes), p.dependientes_max)
        dep_72 = p.uvt_pesos(p.dependiente_uvt * n)
    facturas = (b.facturas_electronicas_total.valor
                if b.facturas_electronicas_total else 0)
    fact_1 = min(porcentaje(facturas, p.facturas_pct),
                 p.uvt_pesos(p.facturas_tope_uvt))
    extra = t.nodo("EXTRA_LIMITE", "Beneficios por fuera del límite 40%",
                   dep_72 + fact_1,
                   f"72 UVT dependientes {dep_72:,} + 1% facturas {fact_1:,}",
                   regla="art. 336 num. 3 y 5")

    netos = t.nodos["ING_NETOS_GENERAL"].valor
    costos = t.nodos["COSTOS_ARRIENDOS"].valor
    return t.nodo(
        "RLG_GENERAL", "Renta líquida gravable cédula general",
        max(0, netos - costos - aplicado - extra),
        f"{netos:,} − costos {costos:,} − aplicado {aplicado:,} − extra {extra:,}",
        insumos=["ING_NETOS_GENERAL", "COSTOS_ARRIENDOS", "APLICADO_40", "EXTRA_LIMITE"],
        regla="art. 336 ET",
    )
