"""Los nombres oficiales de las casillas del formulario 210.

UNA SOLA FUENTE, y por eso vive acá. Tres capas necesitan saber cómo se llama la casilla 29: el
lector del 210 (para explicar qué leyó), el que arma el formulario a radicar, y el resumen del
expediente (que mostraba "R100" y "R131", que un contador reconoce y el titular no). Con el mapa
repetido, una corrección en uno dejaba a los otros dos diciendo otra cosa.

CÓMO SE OBTUVIERON: rasterizando el PDF que entrega el portal y transcribiendo los números
impresos casilla por casilla. El fondo del formulario con los números es una imagen, así que es la
única fuente fiable; los de la cédula de pensiones y la liquidación privada están además
confirmados con la columna "Uso declaración Sugerida" de un reporte real de exógena, donde la DIAN
los nombra tal cual.

Es la versión 18 del formulario (años gravables 2024 y 2025). Si la DIAN renumera, este mapa
cambia y con él las tres capas, que es exactamente lo que se quiere.
"""

from __future__ import annotations

NOMBRES_DE_CASILLA: dict[int, str] = {
    28: "Uno por ciento (1%) de compras con factura electrónica",
    29: "Total patrimonio bruto",
    30: "Deudas",
    31: "Total patrimonio líquido",
    32: "Ingresos brutos (rentas de trabajo)",
    33: "Ingresos no constitutivos de renta (rentas de trabajo)",
    34: "Renta líquida (rentas de trabajo)",
    35: "Aportes voluntarios AFC, FVP y AVC (rentas de trabajo)",
    36: "Otras rentas exentas (rentas de trabajo)",
    37: "Total rentas exentas (rentas de trabajo)",
    38: "Intereses de vivienda (rentas de trabajo)",
    39: "Otras deducciones imputables (rentas de trabajo)",
    40: "Total deducciones imputables (rentas de trabajo)",
    41: "Rentas exentas y deducciones imputables (limitadas) (rentas de trabajo)",
    42: "Renta líquida ordinaria (rentas de trabajo)",
    43: "Ingresos brutos (honorarios)",
    44: "Ingresos no constitutivos de renta (honorarios)",
    45: "Costos y deducciones procedentes (honorarios)",
    46: "Renta líquida (honorarios)",
    47: "Aportes voluntarios AFC, FVP y AVC (honorarios)",
    48: "Otras rentas exentas (honorarios)",
    49: "Total rentas exentas (honorarios)",
    50: "Intereses de vivienda (honorarios)",
    51: "Otras deducciones imputables (honorarios)",
    52: "Total deducciones imputables (honorarios)",
    53: "Rentas exentas y deducciones imputables (limitadas) (honorarios)",
    54: "Renta líquida ordinaria del ejercicio (honorarios)",
    55: "Pérdida líquida del ejercicio (honorarios)",
    56: "Compensaciones por pérdidas (honorarios)",
    57: "Renta líquida ordinaria (honorarios)",
    58: "Ingresos brutos (rentas de capital)",
    59: "Ingresos no constitutivos de renta (rentas de capital)",
    60: "Costos y deducciones procedentes (rentas de capital)",
    61: "Renta líquida (rentas de capital)",
    62: "Rentas líquidas pasivas ECE (rentas de capital)",
    63: "Aportes voluntarios AFC, FVP y AVC (rentas de capital)",
    64: "Otras rentas exentas (rentas de capital)",
    65: "Total rentas exentas (rentas de capital)",
    66: "Intereses de vivienda (rentas de capital)",
    67: "Otras deducciones imputables (rentas de capital)",
    68: "Total deducciones imputables (rentas de capital)",
    69: "Rentas exentas y deducciones imputables (limitadas) (rentas de capital)",
    70: "Renta líquida ordinaria del ejercicio (rentas de capital)",
    71: "Pérdida líquida del ejercicio (rentas de capital)",
    72: "Compensaciones por pérdidas (rentas de capital)",
    73: "Renta líquida ordinaria (rentas de capital)",
    74: "Ingresos brutos (rentas no laborales)",
    75: "Devoluciones, rebajas y descuentos (rentas no laborales)",
    76: "Ingresos no constitutivos de renta (rentas no laborales)",
    77: "Costos y deducciones procedentes (rentas no laborales)",
    78: "Renta líquida (rentas no laborales)",
    79: "Rentas líquidas pasivas ECE (rentas no laborales)",
    80: "Aportes voluntarios AFC, FVP y AVC (rentas no laborales)",
    81: "Otras rentas exentas (rentas no laborales)",
    82: "Total rentas exentas (rentas no laborales)",
    83: "Intereses de vivienda (rentas no laborales)",
    84: "Otras deducciones imputables (rentas no laborales)",
    85: "Total deducciones imputables (rentas no laborales)",
    86: "Rentas exentas y deducciones imputables (limitadas) (rentas no laborales)",
    87: "Renta líquida ordinaria del ejercicio (rentas no laborales)",
    88: "Pérdida líquida del ejercicio (rentas no laborales)",
    89: "Compensaciones por pérdidas (rentas no laborales)",
    90: "Renta líquida ordinaria (rentas no laborales)",
    91: "Renta líquida cédula general",
    92: "Rentas exentas y deducciones imputables (limitadas)",
    93: "Renta líquida ordinaria cédula general",
    94: "Compensación de pérdidas año 2018 y anteriores",
    95: "Compensación por exceso de renta presuntiva",
    96: "Rentas gravables",
    97: "Renta líquida gravable cédula general",
    98: "Renta presuntiva",
    100: "Ingresos no constitutivos de renta (pensiones)",
    103: "Renta líquida gravable cédula de pensiones",
    # Cédula de dividendos y liquidación privada, transcritas del formulario rasterizado. La 111
    # NO es de dividendos: es la renta líquida gravable de TODAS las cédulas, que es la base de la
    # tabla del art. 241.
    104: "Dividendos y participaciones 2016 y anteriores, y otros",
    105: "Ingresos no constitutivos de renta (dividendos)",
    106: "Renta líquida ordinaria año 2016 y anteriores",
    111: "Renta líquida gravable (cédula general, de pensiones y de dividendos, art. 241 ET)",
    112: "Ingresos por ganancias ocasionales del país y del exterior",
    113: "Costos por ganancias ocasionales",
    114: "Ganancias ocasionales no gravadas y exentas",
    115: "Ganancias ocasionales gravables",
    121: "Total impuesto sobre las rentas líquidas gravables",
    126: "Impuesto neto de renta",
    127: "Impuesto de ganancias ocasionales",
    129: "Total impuesto a cargo",
    130: "Anticipo renta liquidado año gravable anterior",
    131: "Saldo a favor del año gravable anterior sin solicitud de devolución",
    132: "Retenciones año gravable a declarar",
    133: "Anticipo renta para el año gravable siguiente",
    134: "Saldo a pagar por impuesto",
    135: "Sanciones",
    # CUIDADO CON ESTAS CUATRO. El saldo va en la 136 y la 137, NO en la 138 y la 139: esas son
    # el número de dependientes y la adición por dependientes. Se transcribieron mal la primera
    # vez y el formulario habría puesto el saldo a pagar en la casilla del conteo de dependientes.
    136: "Total saldo a pagar",
    137: "Total saldo a favor",
    138: "Número de dependientes económicos",
    139: "Adición por dependientes a la casilla 92",
    140: "Superó el tope indicativo del art. 336-1 ET",
    141: "Aporte voluntario",
}


def nombre_de_casilla(numero: int) -> str:
    """El nombre oficial de una casilla, o su número si no está mapeada.

    El respaldo no es un default silencioso: una casilla sin nombre se ve como "casilla 118" en la
    pantalla, que es raro pero honesto, y se corrige agregándola al mapa.
    """
    return NOMBRES_DE_CASILLA.get(numero, f"casilla {numero}")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Los mismos renglones dichos para quien declara una vez al año.
#
# POR QUÉ NO ESTÁN LAS 79. La sugerencia de la DIAN reporta renglones de hecho (lo que te pagaron,
# lo que aportaste, lo que te retuvieron); los demás son totales que el formulario calcula solo, y
# al titular no se le muestran porque nadie se los reportó. Escribir 79 frases, la mitad para filas
# que nunca se pintan, es trabajo que envejece sin que nadie lo lea.
#
# POR QUÉ NO SE COMPONEN. El 210 es regular ("naturaleza (cédula)") y da la tentación de armar el
# nombre juntando las dos mitades. Sale torpe: "Aportes que no cuentan como ingreso de tu trabajo
# como empleado". Escritas a mano se leen; compuestas se entienden a la fuerza.
#
# EL RESPALDO ES EL NOMBRE OFICIAL, y eso es deliberado: si aparece un renglón sin traducir, el
# titular ve el nombre correcto de la DIAN. Es jerga, pero no es falso.
EN_PALABRAS_CASILLA: dict[int, str] = {
    # Patrimonio y deudas.
    29: "Todo lo que tienes, sumado",
    30: "Lo que debes",
    31: "Lo que tienes menos lo que debes",
    # Rentas de trabajo: el sueldo.
    32: "Lo que te pagaron como empleado",
    33: "Salud y pensión que te descontaron del sueldo",
    35: "Lo que ahorraste en AFC, pensión voluntaria o AVC",
    36: "Parte de tu sueldo que no se grava",
    38: "Intereses que pagaste por tu crédito de vivienda",
    39: "Otros gastos que puedes restar del sueldo",
    # Honorarios: trabajo independiente.
    43: "Lo que te pagaron por honorarios",
    44: "Aportes que no cuentan como ingreso, de tus honorarios",
    45: "Costos y gastos de tu trabajo independiente",
    47: "Lo que ahorraste en AFC, pensión voluntaria o AVC, de tus honorarios",
    48: "Parte de tus honorarios que no se grava",
    50: "Intereses de vivienda, imputados a honorarios",
    51: "Otros gastos que puedes restar de tus honorarios",
    # Rentas de capital: intereses, arriendos, rendimientos.
    58: "Intereses, arriendos y rendimientos que recibiste",
    59: "Aportes que no cuentan como ingreso, de esos rendimientos",
    60: "Costos y gastos de esos arriendos o inversiones",
    63: "Lo que ahorraste en AFC, pensión voluntaria o AVC, de tus rendimientos",
    64: "Parte de esos rendimientos que no se grava",
    66: "Intereses de vivienda, imputados a rendimientos",
    67: "Otros gastos que puedes restar de tus rendimientos",
    # Rentas no laborales: todo lo demás.
    74: "Otros ingresos que recibiste",
    75: "Devoluciones y descuentos sobre esos ingresos",
    76: "Aportes que no cuentan como ingreso, de esos otros ingresos",
    77: "Costos y gastos de esos otros ingresos",
    80: "Lo que ahorraste en AFC, pensión voluntaria o AVC, de esos ingresos",
    81: "Parte de esos ingresos que no se grava",
    83: "Intereses de vivienda, imputados a otros ingresos",
    84: "Otros gastos que puedes restar de esos ingresos",
    # Pensiones.
    100: "Aportes de salud que te descontaron de la pensión",
    # Dividendos.
    104: "Dividendos que recibiste",
    105: "Dividendos que no pagan impuesto",
    # Ganancias ocasionales.
    112: "Lo que ganaste vendiendo algo, o lo que te heredaron o rifaste",
    113: "Lo que te costó eso que vendiste",
    114: "La parte de esa ganancia que no se grava",
    # ── LOS TOTALES QUE EL FORMULARIO CALCULA ──
    #
    # No los reporta la sugerencia de la DIAN (los calcula el formulario), así que al principio se
    # dejaron sin traducir. Pero la comparación contra el borrador de la DIAN SÍ los muestra, y ahí
    # el titular se topaba con "Renta líquida ordinaria del ejercicio (honorarios)".
    #
    # Varios se parecen entre sí y no es un descuido: el 210 depura por etapas y cada total es una
    # etapa distinta. Lo que los distingue es QUÉ se restó hasta ese punto, y eso es lo que dicen.
    34: "Tu sueldo después de salud y pensión",
    37: "Total de tu sueldo que no se grava",
    40: "Total de gastos que restaste del sueldo",
    41: "Lo que se descontó del sueldo, ya con el tope",
    42: "Tu sueldo, ya con todo lo que se resta",
    46: "Tus honorarios después de costos",
    49: "Total de tus honorarios que no se gravan",
    52: "Total de gastos que restaste de tus honorarios",
    53: "Lo que se descontó de tus honorarios, ya con el tope",
    54: "Tus honorarios del año, ya depurados",
    57: "Total de tus honorarios ya depurados",
    61: "Tus rendimientos y arriendos después de costos",
    65: "Total de esos rendimientos que no se gravan",
    68: "Total de gastos que restaste de tus rendimientos",
    69: "Lo que se descontó de tus rendimientos, ya con el tope",
    70: "Tus rendimientos del año, ya depurados",
    73: "Total de tus rendimientos ya depurados",
    78: "Tus otros ingresos después de costos",
    82: "Total de esos otros ingresos que no se gravan",
    85: "Total de gastos que restaste de esos ingresos",
    86: "Lo que se descontó de esos ingresos, ya con el tope",
    87: "Tus otros ingresos del año, ya depurados",
    90: "Total de tus otros ingresos ya depurados",
    91: "Todos tus ingresos, ya sin lo que no cuenta",
    92: "Total de beneficios que se aplicaron",
    93: "Tus ingresos después de los beneficios",
    97: "Sobre esto se calcula tu impuesto",
    103: "Sobre esto se calcula el impuesto de tu pensión",
    111: "Sobre esto se aplica la tarifa de la ley",
    115: "Lo que se grava de esas ganancias",
    121: "Impuesto de tus ingresos del año",
    126: "Tu impuesto, ya con los descuentos",
    127: "Impuesto de esas ganancias",
    129: "Total de impuesto que te corresponde",
    # Lo ya pagado y el arrastre del año pasado.
    131: "Saldo a favor del año pasado que no pediste devuelto",
    132: "Lo que ya te retuvieron durante el año",
    133: "Lo que pagaste como anticipo el año pasado",
    136: "Lo que te falta pagar",
    137: "Lo que te devuelven",
    138: "Cuántas personas dependen de ti",
}


def casilla_en_palabras(numero: int) -> str:
    """El renglón dicho para el titular, o el nombre oficial de la DIAN si nadie lo tradujo."""
    return EN_PALABRAS_CASILLA.get(numero, nombre_de_casilla(numero))
