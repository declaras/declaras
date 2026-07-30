"""Cuándo vence la declaración de renta de una persona natural.

FUENTE: Decreto 2229 de 2023, artículo 1.6.1.13.2.15 del Decreto Único Reglamentario 1625 de 2016,
verificado contra el normograma de la DIAN el 2026-07-29. El plazo depende de los DOS ÚLTIMOS
DÍGITOS del NIT que conste en el RUT, "sin tener en cuenta el dígito de verificación", y el decreto
lo expresa en DÍAS HÁBILES de agosto, septiembre y octubre, no en fechas.

EL CALENDARIO ES PERMANENTE. El encabezado de la tabla dice "meses agosto, septiembre y octubre de
2024 y en adelante para cada año subsiguiente", así que la tabla no se toca cada año: lo que cambia
es a qué fecha cae cada día hábil, y eso lo calcula `declaras.calendario` con los festivos del año.

POR QUÉ IMPORTA TANTO ACERTAR: un vencimiento dicho un día tarde cuesta la sanción por
extemporaneidad, cuyo mínimo son 10 UVT ($524.000 en 2026). Es entre siete y nueve veces el precio
del producto, y es plata que el cliente pierde por confiar en una fecha que le dimos mal.

LA ESTRUCTURA ES ARITMÉTICA, y se aprovecha a propósito. Los cincuenta pares de dígitos avanzan de
uno en uno junto con el ordinal del día hábil, en tres bloques corridos. Escribir cincuenta filas a
mano tiene un modo de falla peor que el de la aritmética: un typo en una fila no se nota nunca.
La transcripción literal del decreto vive en `tests/unit/tax/test_vencimientos.py`, que comprueba
los cien dígitos posibles contra la tabla copiada del texto oficial.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from declaras.calendario import dia_habil_del_mes
from declaras.domain.errors import ValidationError


class _Bloque(NamedTuple):
    """Un tramo corrido de la tabla del decreto."""

    # Cuántos pares de dígitos cubre el bloque.
    pares: int
    mes: int
    # Ordinal del día hábil que le toca al PRIMER par del bloque. De ahí avanza de uno en uno.
    primer_ordinal: int


# Los tres bloques del art. 1.6.1.13.2.15, en orden:
#
#   01-02 … 25-26   del  7.º al 19.º día hábil de agosto        (13 pares)
#   27-28 … 65-66   del  1.º al 20.º día hábil de septiembre    (20 pares)
#   67-68 … 99-00   del  1.º al 17.º día hábil de octubre       (17 pares)
#
# 13 + 20 + 17 = 50, que son los pares que hay entre 00 y 99. Si esa suma no cuadrara, la tabla
# estaría incompleta y algún contribuyente no tendría fecha.
_BLOQUES: tuple[_Bloque, ...] = (
    _Bloque(pares=13, mes=8, primer_ordinal=7),
    _Bloque(pares=20, mes=9, primer_ordinal=1),
    _Bloque(pares=17, mes=10, primer_ordinal=1),
)

PARES_DE_DIGITOS = 50


def _indice_del_par(dos_digitos: int) -> int:
    """El par al que pertenecen los dos últimos dígitos, de 0 a 49.

    Los pares del decreto son (01,02), (03,04) … (99,00): el CERO cierra la tabla junto al 99, no
    la abre. El módulo 100 sobre `dos_digitos - 1` lo resuelve sin un caso especial: para 00 da 99,
    que cae en el último par. Escrito con un `if` aparte, el 00 es justo el que se olvida.
    """
    return ((dos_digitos - 1) % 100) // 2


def _mes_y_ordinal(dos_digitos: int) -> tuple[int, int]:
    indice = _indice_del_par(dos_digitos)
    for bloque in _BLOQUES:
        if indice < bloque.pares:
            return bloque.mes, bloque.primer_ordinal + indice
        indice -= bloque.pares
    raise AssertionError(  # pragma: no cover - los bloques cubren los 50 pares
        f"Los bloques del decreto no cubren el par {indice}: la tabla está incompleta."
    )


def dos_ultimos_digitos(numero_documento: str) -> int:
    """Los dos últimos dígitos del NIT, que para una persona natural es su cédula.

    NO ACEPTA EL DÍGITO DE VERIFICACIÓN, y por eso valida en vez de recortar: el decreto dice
    expresamente "sin tener en cuenta el dígito de verificación", así que un NIT con el DV pegado
    ("10998877761" en vez de "1099887776") corre la fecha de vencimiento a otro par de dígitos.
    Recortarlo por nuestra cuenta adivinaría cuál de los dígitos es el DV; exigir el número limpio
    obliga a que quien lo arma lo diga.
    """
    limpio = numero_documento.strip().replace(".", "").replace(" ", "")
    if "-" in limpio:
        raise ValidationError(
            "El número de documento trae dígito de verificación y el plazo se calcula sin él "
            "(Decreto 2229 de 2023). Pásalo sin el guion.",
            details={"recibido": numero_documento},
        )
    if not limpio.isdigit():
        raise ValidationError(
            "El número de documento tiene que ser solo dígitos para calcular el vencimiento.",
            details={"recibido": numero_documento},
        )
    return int(limpio[-2:]) if len(limpio) >= 2 else int(limpio)


def vencimiento_de(numero_documento: str, anio_gravable: int) -> date:
    """La fecha límite para presentar y pagar la declaración de ese año gravable.

    El plazo cae en el año SIGUIENTE al gravable: la declaración del año gravable 2025 se presenta
    entre agosto y octubre de 2026.
    """
    mes, ordinal = _mes_y_ordinal(dos_ultimos_digitos(numero_documento))
    return dia_habil_del_mes(anio_gravable + 1, mes, ordinal)
