from typing import TypedDict

from declaras.caso import CasoTributario
from declaras.motor import Liquidacion, Nodo
from declaras.parametros.en_palabras import en_palabras
from declaras.render.orden import ORDEN_CASILLAS

# Casillas que son un sí/no del motor, no una cifra: "Valor: 1" no es auditable.
_BOOLEANAS = frozenset({"OBLIGADO_DECLARAR"})

# Markdown no tiene escape universal, pero CommonMark respeta la barra invertida sobre
# puntuación ASCII. Se escapa lo que fabrica estructura (encabezados, énfasis, enlaces,
# tablas, código) y lo que colaría HTML crudo en fronts que rendericen con html:true.
_MD_ESPECIALES = str.maketrans({c: "\\" + c for c in "\\#*_[]<>|`"})


def _md_texto(x: object) -> str:
    """Escapa texto no confiable: llega de extracción LLM y del API.

    Cubre la identidad del contribuyente y los mensajes de flag, que interpolan
    nombres de terceros (`empleador_nombre`, `pagador`, `entidad`, `sociedad_nombre`,
    `inmueble`). Colapsa todo whitespace — un `\\n` fabricaría una sección falsa de la
    memoria — y luego escapa los metacaracteres.
    """
    return " ".join(str(x).split()).translate(_MD_ESPECIALES)


def tipo_de_valor(codigo: str) -> str:
    """`si_no` o `pesos`. Sin esto, quien pinta tiene que adivinar si un `1` es un peso o un sí.

    Vive aquí porque `_BOOLEANAS` vive aquí, y duplicar esa lista en el API es garantizar que las
    dos se separen: OBLIGADO_DECLARAR se veía como "$ 1".
    """
    return "si_no" if codigo in _BOOLEANAS else "pesos"


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
            f"del {caso.anio_gravable}: no son la misma declaración."
        )


class PasoDelCalculo(TypedDict):
    """Un paso del cálculo tal como sale hacia afuera.

    Tiene forma declarada porque el API lo serializa: es un contrato con el front, no un dict
    interno. Sin esto, agregar o renombrar una llave aquí rompe la interfaz en silencio.
    """

    codigo: str
    etiqueta: str
    # El mismo paso dicho para el titular. Viaja junto a la etiqueta técnica, no en vez de ella:
    # el contador necesita "INCRNGO" para defender la cifra y el titular necesita no leerlo.
    en_palabras: str
    valor: int
    valor_texto: str
    # Qué clase de valor es. Sin esto, quien pinta la memoria tiene que adivinar si un `1` son
    # un peso o un sí, y OBLIGADO_DECLARAR se veía como "$1".
    tipo: str
    formula: str
    insumos: list[str]
    regla: str | None


def casillas(liq: Liquidacion) -> list[PasoDelCalculo]:
    filas: list[PasoDelCalculo] = []
    for codigo in ORDEN_CASILLAS:
        if codigo in liq.nodos:
            n = liq.nodos[codigo]
            filas.append(
                {
                    "codigo": n.codigo,
                    "etiqueta": n.etiqueta,
                    "en_palabras": en_palabras(n.codigo, n.etiqueta),
                    "valor": n.valor,
                    "valor_texto": _valor_texto(n),
                    "tipo": tipo_de_valor(n.codigo),
                    "formula": n.formula,
                    "insumos": n.insumos,
                    "regla": n.regla,
                }
            )
    return filas


def memoria_markdown(liq: Liquidacion, caso: CasoTributario) -> str:
    _verificar_pareja(liq, caso)
    c = caso.contribuyente
    lineas = [
        f"# Memoria de cálculo — {_md_texto(c.nombre)} "
        f"({_md_texto(c.tipo_doc)} {_md_texto(c.num_doc)})",
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
            lineas.append(f"- **[{fl.severidad}] {fl.codigo}**: {_md_texto(fl.mensaje)}")
    return "\n".join(lineas)
