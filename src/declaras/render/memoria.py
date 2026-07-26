from declaras.caso import CasoTributario
from declaras.motor import Liquidacion
from declaras.render.orden import ORDEN_CASILLAS


def casillas(liq: Liquidacion) -> list[dict]:
    filas = []
    for codigo in ORDEN_CASILLAS:
        if codigo in liq.nodos:
            n = liq.nodos[codigo]
            filas.append({"codigo": n.codigo, "etiqueta": n.etiqueta,
                          "valor": n.valor, "formula": n.formula,
                          "insumos": n.insumos, "regla": n.regla})
    return filas


def memoria_markdown(liq: Liquidacion, caso: CasoTributario) -> str:
    lineas = [
        f"# Memoria de cálculo — {caso.contribuyente.nombre} "
        f"({caso.contribuyente.tipo_doc} {caso.contribuyente.num_doc})",
        f"Año gravable {liq.anio_gravable} · elecciones: "
        f"art387={'sí' if liq.elecciones.usar_387 else 'no'}, "
        f"72UVT={'sí' if liq.elecciones.usar_72uvt else 'no'}",
        "",
    ]
    for f in casillas(liq):
        regla = f" _({f['regla']})_" if f["regla"] else ""
        lineas.append(f"## {f['codigo']} — {f['etiqueta']}{regla}")
        lineas.append(f"**Valor:** {f['valor']:,}")
        lineas.append(f"**Cómo:** {f['formula']}")
        if f["insumos"]:
            lineas.append(f"**Insumos:** {', '.join(f['insumos'])}")
        lineas.append("")
    if liq.flags:
        lineas.append("## Alertas")
        for fl in liq.flags:
            lineas.append(f"- **[{fl.severidad}] {fl.codigo}**: {fl.mensaje}")
    return "\n".join(lineas)
