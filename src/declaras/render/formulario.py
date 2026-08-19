"""El formulario 210 que se va a radicar: los nodos del motor en las casillas oficiales.

POR QUE ESTO NO ES LA MEMORIA DE CALCULO

La memoria presenta el cálculo por conceptos (`RLG_GENERAL`, `IMPUESTO_241`) y sirve para
auditarlo: dice de dónde sale cada cifra y con qué norma. Pero no es lo que se radica. Lo que se
radica son casillas numeradas, y el mapeo de una cosa a la otra estaba declarado pendiente.

Y HABIA UNA TERCERA COSA QUE PARECIA LA MISMA

La exógena trae la columna "Uso declaración Sugerida", donde la DIAN dice a qué renglón va cada
valor que un tercero reportó, y el sistema lo mostraba como "renglones del formulario 210". Eso es
LO QUE LA DIAN SUGERIRIA con lo que ella sabe, no lo que se va a declarar. Medido en un caso real:
la casilla 29 traía $16.433.767 por un lado y $11.862.658 por el otro, y la 32 se diferenciaba en
$7.440.277. Las dos cifras eran correctas —una suma todo lo reportado, la otra solo lo que quedó
tras decidir— pero presentarlas igual invita a desconfiar de las dos.

ESTE MODULO NO CALCULA NADA. Reparte lo que el motor ya liquidó. Si acá saliera una cifra distinta
habría dos verdades sobre el mismo impuesto, y la de un módulo de presentación no puede ganarle a
la del motor.

LA GARANTIA DE QUE CUADRA son las identidades del propio formulario, que se prueban sobre los
casos golden: el patrimonio líquido es el bruto menos las deudas, la renta líquida es los ingresos
menos lo no constitutivo, la renta de la cédula general es la suma de sus cuatro columnas. Un
mapeo desincronizado rompe esas restas.

LO QUE NO ESTA MAPEADO SE DICE. El 210 tiene 141 casillas y acá se llenan las que el motor
alimenta. Las de ganancias ocasionales y las de la cédula de dividendos por subcédula quedan
fuera, y `CASILLAS_SIN_MAPEAR` las enumera: un hueco declarado se puede cerrar, uno silencioso se
descubre cuando alguien radica un formulario incompleto.
"""

from __future__ import annotations

from dataclasses import dataclass

from declaras.caso import CasoTributario
from declaras.motor import Liquidacion
from declaras.parametros.casillas import nombre_de_casilla

# Los nombres oficiales de las casillas que este módulo llena. Están transcritos del formulario
# real (versión 18, año gravable 2024/2025) rasterizando el PDF que entrega el portal: el fondo
# con los números de casilla es una imagen, así que es la única fuente fiable. El lector del 210
# usa el mismo mapa para la operación inversa.
# Las casillas que este módulo llena. El nombre sale de `parametros.casillas`, que es la única
# fuente: el lector del 210 y el resumen del expediente usan el mismo mapa.
_NUMEROS = (
    # Patrimonio
    29,
    30,
    31,
    # Cédula general: rentas de trabajo
    32,
    33,
    34,
    36,
    37,
    40,
    41,
    42,
    # Honorarios
    43,
    46,
    # Rentas de capital
    58,
    59,
    60,
    61,
    # Rentas no laborales
    74,
    78,
    # Totales de la cédula general
    91,
    92,
    93,
    97,
    # Pensiones y dividendos
    103,
    104,
    111,
    # Liquidación privada. El saldo va en la 136 y la 137: la 138 y la 139 son el número de
    # dependientes y la adición por dependientes, y se transcribieron mal la primera vez.
    121,
    126,
    131,
    132,
    133,
    136,
    137,
)
CASILLAS_DEL_210: dict[int, str] = {n: nombre_de_casilla(n) for n in _NUMEROS}


# Las casillas del formulario que este módulo NO llena todavía, con el porqué. Un hueco declarado
# se puede cerrar; uno silencioso se descubre cuando alguien radica un formulario incompleto.
CASILLAS_SIN_MAPEAR: dict[str, str] = {
    "ganancias ocasionales (112 a 120)": (
        "el motor no liquida ganancias ocasionales: una venta de inmueble o una herencia "
        "necesitan su propio cálculo"
    ),
    "dividendos por subcédula (105 a 110)": (
        "el motor calcula los dividendos agregados, y el formulario los separa por año de "
        "origen y por régimen"
    ),
    "anticipo del año anterior y sanciones (128 a 130, 134 y 135)": (
        "el anticipo liquidado el año pasado y las sanciones son datos de la declaración "
        "previa, que todavía no se leen. El saldo a favor arrastrado (131) sí se llena: lo "
        "reporta la exógena"
    ),
}


@dataclass(frozen=True)
class Casilla:
    """Una casilla del formulario, con su número, su nombre oficial y de dónde salió."""

    numero: int
    nombre: str
    valor: int
    # El nodo del motor que la alimenta, para poder volver del formulario a la memoria de cálculo.
    nodo: str | None = None


def formulario_210(liq: Liquidacion, caso: CasoTributario) -> list[Casilla]:
    """Las casillas del 210, en el orden en que aparecen en el formulario.

    El desglose por columna sale del caso y no del motor, y esa es la parte que no es obvia: el
    motor liquida la cédula general AGREGADA (art. 336 la trata como una sola base), mientras el
    formulario la separa en cuatro columnas por tipo de renta. Los ingresos brutos de cada columna
    se suman de los hechos del caso, que es donde está la clasificación.
    """
    ingresos = _ingresos_por_columna(caso)
    deudas = sum(d.saldo_31dic for d in caso.patrimonio.deudas)
    saldo = liq.valor("SALDO")

    # Las exenciones y deducciones las calcula el motor sobre la cédula completa y el formulario
    # las pide en la columna de rentas de trabajo, que es donde nacen (el 25% del art. 206 y las
    # deducciones del art. 387 son laborales). Ponerlas repartidas entre columnas sería inventar
    # un reparto que la ley no hace.
    exentas = liq.valor("EXENTA_25")
    deducciones = liq.valor("DEDUCCIONES_LIMITADAS")
    limitadas = liq.valor("APLICADO_40")

    filas: list[tuple[int, int, str | None]] = [
        (29, liq.valor("PATRIMONIO_BRUTO"), "PATRIMONIO_BRUTO"),
        (30, deudas, None),
        (31, liq.valor("PATRIMONIO_LIQUIDO"), "PATRIMONIO_LIQUIDO"),
        # Columna de rentas de trabajo
        (32, ingresos["trabajo"], None),
        (33, liq.valor("INCR_TOTAL"), "INCR_TOTAL"),
        (34, max(ingresos["trabajo"] - liq.valor("INCR_TOTAL"), 0), None),
        (36, exentas, "EXENTA_25"),
        (37, exentas, "EXENTA_25"),
        (40, deducciones, "DEDUCCIONES_LIMITADAS"),
        (41, limitadas, "APLICADO_40"),
        (42, max(ingresos["trabajo"] - liq.valor("INCR_TOTAL") - limitadas, 0), None),
        # Columna de honorarios
        (43, ingresos["honorarios"], None),
        (46, ingresos["honorarios"], None),
        # Columna de rentas de capital
        (58, ingresos["capital"], None),
        (59, 0, None),
        (60, liq.valor("COSTOS_ARRIENDOS"), "COSTOS_ARRIENDOS"),
        (61, max(ingresos["capital"] - liq.valor("COSTOS_ARRIENDOS"), 0), None),
        # Columna de rentas no laborales
        (74, ingresos["no_laborales"], None),
        (78, ingresos["no_laborales"], None),
    ]

    # Los totales de la cédula general: la suma de las cuatro columnas, que es la identidad que
    # el formulario verifica solo.
    renta_trabajo = max(ingresos["trabajo"] - liq.valor("INCR_TOTAL"), 0)
    renta_capital = max(ingresos["capital"] - liq.valor("COSTOS_ARRIENDOS"), 0)
    cedula_general = (
        renta_trabajo + ingresos["honorarios"] + renta_capital + ingresos["no_laborales"]
    )

    filas += [
        (91, cedula_general, None),
        (92, limitadas, "APLICADO_40"),
        (93, max(cedula_general - limitadas, 0), None),
        (97, liq.valor("RLG_GENERAL"), "RLG_GENERAL"),
        (103, liq.valor("RLG_PENSIONES"), "RLG_PENSIONES"),
        (104, liq.valor("DIV_NO_GRAVADOS"), "DIV_NO_GRAVADOS"),
        (111, liq.valor("DIV_GRAVADOS"), "DIV_GRAVADOS"),
        (121, liq.valor("IMPUESTO_241") + liq.valor("IMP_DIV_35"), None),
        (126, liq.valor("IMPUESTO_NETO"), "IMPUESTO_NETO"),
        # LA 131 NO SE IMPRIMIA Y EL ARRASTRE SE VEIA COMO UN ERROR. El motor sí restaba el saldo
        # a favor del año anterior al calcular la 137, pero la casilla que lo declara no salía en
        # el formulario: quien lo revisara veía un total a favor mayor que la resta de las cifras
        # impresas y no tenía de dónde sacar la diferencia. Son dos hechos distintos —lo que se
        # arrastra y lo que resulta— y el formulario pide los dos.
        (131, caso.creditos.saldo_favor_anterior, None),
        (132, liq.valor("RETENCIONES"), "RETENCIONES"),
        (133, liq.valor("ANTICIPO_SIGUIENTE"), "ANTICIPO_SIGUIENTE"),
        # Las dos casillas de saldo son EXCLUYENTES: el formulario tiene una para pagar y otra
        # para devolver, y llenar las dos es un formulario que no cuadra.
        #
        # SON LA 136 Y LA 137, no la 138 y la 139. Se transcribieron mal la primera vez, y la
        # prueba que exige nombre oficial en cada casilla lo destapó: la 138 es el número de
        # dependientes económicos y la 139 la adición por dependientes a la casilla 92. El
        # formulario habría llevado el saldo a pagar a la casilla del conteo de dependientes.
        (136, max(saldo, 0), "SALDO"),
        (137, max(-saldo, 0), "SALDO"),
    ]

    return [
        Casilla(numero=n, nombre=CASILLAS_DEL_210[n], valor=v, nodo=nodo) for n, v, nodo in filas
    ]


def _ingresos_por_columna(caso: CasoTributario) -> dict[str, int]:
    """Los ingresos brutos repartidos en las cuatro columnas de la cédula general.

    El reparto sale del TIPO de hecho, que es lo que el formulario usa para separar las columnas:
    un salario es renta de trabajo, un rendimiento financiero o un arriendo son rentas de capital.
    Los honorarios y los servicios irían a su propia columna, pero el motor no los liquida todavía
    (van a mano), así que esa columna llega en cero y no se inventa una cifra.
    """
    return {
        "trabajo": sum(x.salarios for x in caso.laborales),
        "honorarios": 0,
        "capital": (
            sum(x.valor for x in caso.rendimientos) + sum(x.canon_total for x in caso.arriendos)
        ),
        "no_laborales": 0,
    }
