"""Días hábiles y festivos de Colombia.

POR QUÉ ESTE MÓDULO EXISTE Y NO ES UNA TABLA DE FECHAS. El calendario de vencimientos de la DIAN
(Decreto 2229 de 2023, art. 1.6.1.13.2.15) NO está escrito en fechas: dice "séptimo día hábil de
agosto", "vigésimo día hábil de septiembre". Y es permanente ("agosto, septiembre y octubre de 2024
y en adelante para cada año subsiguiente"), así que la tabla no cambia pero las fechas sí, cada año.

Copiar las fechas de un año a mano tiene dos modos de falla, los dos caros: se envejece en silencio
(el año que viene el producto avisa el vencimiento equivocado) y se equivoca en el año en curso si
quien copió contó mal un festivo. Un vencimiento mal dicho por un día cuesta la sanción mínima:
$524.000 en 2026, entre siete y nueve veces lo que cuesta el producto.

Vive en la raíz del paquete, al lado de `dinero`, porque no es tributario: es calendario civil. Lo
usa `tax.vencimientos` para los plazos y sirve igual para contar meses de mora.

LAS TRES CLASES DE FESTIVO COLOMBIANO, que es lo único que hay que entender acá:

  fijos                    1 de enero, 1 de mayo, 20 de julio, 7 de agosto, 8 y 25 de diciembre.
                           Caen donde caen, incluso en domingo.
  trasladables al lunes    Ley 51 de 1983 ("Ley Emiliani"): Reyes, San José, San Pedro, Asunción,
                           Día de la Raza, Todos los Santos, Independencia de Cartagena. Si no caen
                           lunes, se corren al lunes siguiente.
  derivados de la Pascua   Jueves y Viernes Santo no se trasladan; Ascensión, Corpus Christi y
                           Sagrado Corazón sí, y por eso su desplazamiento desde la Pascua ya viene
                           contado en lunes (43, 64 y 71 días).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

# Festivos de fecha fija que NO se trasladan. La ley los deja donde caen.
_FIJOS: tuple[tuple[int, int], ...] = (
    (1, 1),  # Año Nuevo
    (5, 1),  # Día del Trabajo
    (7, 20),  # Independencia
    (8, 7),  # Batalla de Boyacá
    (12, 8),  # Inmaculada Concepción
    (12, 25),  # Navidad
)

# Festivos de fecha fija que se corren al lunes siguiente si no caen lunes (Ley 51 de 1983).
_TRASLADABLES: tuple[tuple[int, int], ...] = (
    (1, 6),  # Reyes Magos
    (3, 19),  # San José
    (6, 29),  # San Pedro y San Pablo
    (8, 15),  # Asunción de la Virgen
    (10, 12),  # Día de la Raza
    (11, 1),  # Todos los Santos
    (11, 11),  # Independencia de Cartagena
)

# Días después del Domingo de Pascua. Los dos primeros son la Semana Santa y caen donde caen; los
# tres últimos ya traen el traslado al lunes incorporado en el número, que es como se publican.
_DESDE_PASCUA_SIN_TRASLADO: tuple[int, ...] = (-3, -2)  # Jueves y Viernes Santo
_DESDE_PASCUA_EN_LUNES: tuple[int, ...] = (43, 64, 71)  # Ascensión, Corpus, Sagrado Corazón


def domingo_de_pascua(anio: int) -> date:
    """Domingo de Resurrección por el algoritmo de Meeus/Butcher (calendario gregoriano).

    Se calcula en vez de tabularse porque la tabla habría que extenderla cada año, y un año que
    falte no da error: da el festivo equivocado. El algoritmo es exacto entre 1583 y 4099.
    """
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ele = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ele) // 451
    mes, dia = divmod(h + ele - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def _al_lunes(dia: date) -> date:
    """El lunes de esa semana o el siguiente. Un lunes se queda donde está."""
    return dia + timedelta(days=(7 - dia.weekday()) % 7)


@lru_cache(maxsize=32)
def festivos(anio: int) -> frozenset[date]:
    """Todos los festivos de Colombia de un año.

    Se memoiza porque contar días hábiles recorre el mes día por día y volvería a construir el
    conjunto en cada paso.
    """
    pascua = domingo_de_pascua(anio)
    dias = {date(anio, mes, dia) for mes, dia in _FIJOS}
    dias |= {_al_lunes(date(anio, mes, dia)) for mes, dia in _TRASLADABLES}
    dias |= {pascua + timedelta(days=n) for n in _DESDE_PASCUA_SIN_TRASLADO}
    dias |= {pascua + timedelta(days=n) for n in _DESDE_PASCUA_EN_LUNES}
    return frozenset(dias)


def es_habil(dia: date) -> bool:
    """Día hábil: de lunes a viernes y que no sea festivo.

    El sábado NO es hábil para efectos de estos plazos: el decreto habla de días hábiles y las
    entidades autorizadas para recaudar no operan el fin de semana.
    """
    return dia.weekday() < 5 and dia not in festivos(dia.year)


def dia_habil_del_mes(anio: int, mes: int, ordinal: int) -> date:
    """El n-ésimo día hábil de un mes. `ordinal` empieza en 1.

    Falla si el mes no tiene tantos días hábiles, en vez de devolver el último: un plazo que no
    existe es un error del calendario o de la tabla del decreto, y devolver una fecha cercana lo
    volvería indetectable. Agosto de 2026 tiene 19 días hábiles y la tabla pide justo hasta el 19,
    así que el margen es cero y esta guarda es la que avisaría si un festivo nuevo lo reduce.
    """
    if ordinal < 1:
        raise ValueError(f"El ordinal de un día hábil empieza en 1; llegó {ordinal}.")
    dia = date(anio, mes, 1)
    vistos = 0
    while dia.month == mes:
        if es_habil(dia):
            vistos += 1
            if vistos == ordinal:
                return dia
        dia += timedelta(days=1)
    raise ValueError(
        f"{mes:02d}/{anio} solo tiene {vistos} días hábiles y se pidió el número {ordinal}. "
        "Si la tabla del decreto pide ese ordinal, hay que revisar los festivos del año."
    )
