from declaras.caso import CasoTributario
from declaras.dinero import pesos
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
            "El decreto del componente inflacionario AG 2025 no ha salido: "
            "se usa 0% (conservador). Actualizar ag2025.yaml cuando se expida.",
        )
    pct_ci = p.componente_inflacionario or 0.0
    ci = t.nodo(
        "INCR_CI", "INCRNGO componente inflacionario de rendimientos",
        pesos(total_rend * pct_ci),
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

    t.nodo("CAP_40", "Límite exentas+deducciones (menor entre 40% y 1.340 UVT)",
           min(pesos(netos * p.limite_general_pct), p.uvt_pesos(p.limite_general_uvt)),
           f"min(40% × {netos:,}, 1.340 UVT = {p.uvt_pesos(p.limite_general_uvt):,})",
           insumos=["ING_NETOS_GENERAL"], regla="art. 336 num. 3")
