from declaras.caso import CasoTributario
from declaras.motor import Liquidacion, Nodo
from declaras.render.orden import ORDEN_CASILLAS

# Casillas que son un sí/no del motor, no una cifra: "Valor: 1" no es auditable.
_BOOLEANAS = frozenset({"OBLIGADO_DECLARAR"})

# Markdown no tiene escape universal, pero CommonMark respeta la barra invertida sobre
# puntuación ASCII. Se escapa lo que fabrica estructura (encabezados, énfasis, enlaces,
# tablas, código) y lo que colaría HTML crudo en fronts que rendericen con html:true.
_MD_ESPECIALES = str.maketrans({c: "\\" + c for c in "\\#*_[]<>|`"})


def _md_identidad(x: object) -> str:
    """Escapa identidad del contribuyente: llega de extracción LLM y del API.

    Colapsa todo whitespace — un `\\n` en el nombre fabricaría una sección falsa de
    la memoria — y luego escapa los metacaracteres.
    """
    return " ".join(str(x).split()).translate(_MD_ESPECIALES)


def _valor_texto(n: Nodo) -> str:
    """Valor listo para leer. El int crudo se conserva aparte, para el API."""
    if n.codigo in _BOOLEANAS:
        return "Sí" if n.valor else "No"
    return f"{n.valor:,}"


def _verificar_pareja(liq: Liquidacion, caso: CasoTributario) -> None:
    """Evita rotular la liquidación de un año con los datos de otro.

    La identidad todavía no se puede verificar: `Liquidacion` no lleva `num_doc` ni
    referencia al caso, así que un caso ajeno del mismo año pasa este guard. Cuando
    `Liquidacion` lleve esa referencia, el chequeo del documento va aquí.
    """
    if liq.anio_gravable != caso.anio_gravable:
        raise ValueError(
            f"La liquidación es del año gravable {liq.anio_gravable} pero el caso es "
            f"del {caso.anio_gravable}: no son la misma declaración.")


def casillas(liq: Liquidacion) -> list[dict]:
    filas = []
    for codigo in ORDEN_CASILLAS:
        if codigo in liq.nodos:
            n = liq.nodos[codigo]
            filas.append({"codigo": n.codigo, "etiqueta": n.etiqueta,
                          "valor": n.valor, "valor_texto": _valor_texto(n),
                          "formula": n.formula, "insumos": n.insumos,
                          "regla": n.regla})
    return filas


def memoria_markdown(liq: Liquidacion, caso: CasoTributario) -> str:
    _verificar_pareja(liq, caso)
    c = caso.contribuyente
    lineas = [
        f"# Memoria de cálculo — {_md_identidad(c.nombre)} "
        f"({_md_identidad(c.tipo_doc)} {_md_identidad(c.num_doc)})",
        "",
        f"Año gravable {liq.anio_gravable} · elecciones: "
        f"art387={'sí' if liq.elecciones.usar_387 else 'no'}, "
        f"72UVT={'sí' if liq.elecciones.usar_72uvt else 'no'}",
        "",
    ]
    # Línea en blanco entre Valor/Cómo/Insumos: sin ella el soft-break de CommonMark
    # los corre en un solo párrafo.
    for f in casillas(liq):
        regla = f" _({f['regla']})_" if f["regla"] else ""
        lineas += [f"## {f['codigo']} — {f['etiqueta']}{regla}", ""]
        lineas += [f"**Valor:** {f['valor_texto']}", ""]
        lineas += [f"**Cómo:** {f['formula']}", ""]
        if f["insumos"]:
            lineas += [f"**Insumos:** {', '.join(f['insumos'])}", ""]
    if liq.flags:
        lineas += ["## Alertas", ""]
        for fl in liq.flags:
            lineas.append(f"- **[{fl.severidad}] {fl.codigo}**: {fl.mensaje}")
    return "\n".join(lineas)
