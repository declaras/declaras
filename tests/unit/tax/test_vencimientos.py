"""El vencimiento de la declaración según los dos últimos dígitos del NIT.

ESTE ARCHIVO ES LA AUDITORÍA DE LA TABLA. `tax.vencimientos` la resuelve con aritmética porque los
cincuenta pares del decreto avanzan de uno en uno, y una tabla de cincuenta filas escrita a mano
esconde los typos. Acá está la transcripción LITERAL del Decreto 2229 de 2023 (art. 1.6.1.13.2.15
del DUR 1625 de 2016), copiada del normograma de la DIAN el 2026-07-29, y se comprueban los cien
dígitos posibles contra ella.

Si alguien cambia la aritmética, esto lo atrapa. Si el decreto cambia, hay que actualizar las dos
cosas, y que sean dos es el punto.
"""

from __future__ import annotations

from datetime import date

import pytest

from declaras.calendario import es_habil
from declaras.domain.errors import ValidationError
from declaras.tax.vencimientos import (
    PARES_DE_DIGITOS,
    dos_ultimos_digitos,
    vencimiento_de,
)

# Transcripción literal del decreto: (dígitos como los escribe la norma, mes, ordinal hábil).
# El texto oficial dice "Séptimo día hábil de agosto", "Vigésimo día hábil de septiembre", etc.
TABLA_DEL_DECRETO: tuple[tuple[str, int, int], ...] = (
    ("01 y 02", 8, 7),
    ("03 y 04", 8, 8),
    ("05 y 06", 8, 9),
    ("07 y 08", 8, 10),
    ("09 y 10", 8, 11),
    ("11 y 12", 8, 12),
    ("13 y 14", 8, 13),
    ("15 y 16", 8, 14),
    ("17 y 18", 8, 15),
    ("19 y 20", 8, 16),
    ("21 y 22", 8, 17),
    ("23 y 24", 8, 18),
    ("25 y 26", 8, 19),
    ("27 y 28", 9, 1),
    ("29 y 30", 9, 2),
    ("31 y 32", 9, 3),
    ("33 y 34", 9, 4),
    ("35 y 36", 9, 5),
    ("37 y 38", 9, 6),
    ("39 y 40", 9, 7),
    ("41 y 42", 9, 8),
    ("43 y 44", 9, 9),
    ("45 y 46", 9, 10),
    ("47 y 48", 9, 11),
    ("49 y 50", 9, 12),
    ("51 y 52", 9, 13),
    ("53 y 54", 9, 14),
    ("55 y 56", 9, 15),
    ("57 y 58", 9, 16),
    ("59 y 60", 9, 17),
    ("61 y 62", 9, 18),
    ("63 y 64", 9, 19),
    ("65 y 66", 9, 20),
    ("67 y 68", 10, 1),
    ("69 y 70", 10, 2),
    ("71 y 72", 10, 3),
    ("73 y 74", 10, 4),
    ("75 y 76", 10, 5),
    ("77 y 78", 10, 6),
    ("79 y 80", 10, 7),
    ("81 y 82", 10, 8),
    ("83 y 84", 10, 9),
    ("85 y 86", 10, 10),
    ("87 y 88", 10, 11),
    ("89 y 90", 10, 12),
    ("91 y 92", 10, 13),
    ("93 y 94", 10, 14),
    ("95 y 96", 10, 15),
    ("97 y 98", 10, 16),
    ("99 y 00", 10, 17),
)


def _digitos(texto: str) -> tuple[int, int]:
    """ "01 y 02" -> (1, 2)."""
    izquierda, derecha = texto.split(" y ")
    return int(izquierda), int(derecha)


def test_la_tabla_transcrita_cubre_los_cincuenta_pares() -> None:
    """Cincuenta pares entre 00 y 99. Si falta uno, algún contribuyente no tiene fecha."""
    assert len(TABLA_DEL_DECRETO) == PARES_DE_DIGITOS
    cubiertos = {d for fila in TABLA_DEL_DECRETO for d in _digitos(fila[0])}
    assert cubiertos == set(range(100))


@pytest.mark.parametrize(("texto", "mes", "ordinal"), TABLA_DEL_DECRETO)
def test_cada_par_de_digitos_cae_en_el_dia_habil_que_dice_el_decreto(
    texto: str, mes: int, ordinal: int
) -> None:
    """La aritmética de `_mes_y_ordinal` contra la tabla literal, par por par.

    Se verifica con el año gravable 2025 (plazos en 2026) recalculando el día hábil desde cero:
    se cuentan los hábiles del mes hasta el ordinal y se compara con lo que devuelve la función.
    """
    esperado = _n_esimo_habil(2026, mes, ordinal)
    for digito in _digitos(texto):
        # Un documento cualquiera que termine en esos dos dígitos.
        documento = f"10998877{digito:02d}"
        assert vencimiento_de(documento, 2025) == esperado, (
            f"el documento terminado en {digito:02d} debería vencer el {esperado} "
            f"({ordinal}.º día hábil del mes {mes})"
        )


def _n_esimo_habil(anio: int, mes: int, ordinal: int) -> date:
    """Cuenta los días hábiles a mano, sin usar el módulo que se está probando."""
    from calendar import monthrange

    vistos = 0
    for dia in range(1, monthrange(anio, mes)[1] + 1):
        fecha = date(anio, mes, dia)
        if es_habil(fecha):
            vistos += 1
            if vistos == ordinal:
                return fecha
    raise AssertionError(f"{mes}/{anio} no tiene {ordinal} días hábiles")


# ── el cero, que es el que se olvida ──────────────────────────────────────────────────────────────


def test_el_cero_cierra_la_tabla_y_no_la_abre() -> None:
    """Los pares son (01,02) … (99,00): el 00 va con el 99, al final.

    Es el error clásico de esta tabla. Tratado como "primer par" le daría a quien termina en 00 el
    vencimiento de agosto en vez del de octubre, o sea dos meses antes de lo que le corresponde: se
    le diría que está en mora cuando no lo está.
    """
    assert vencimiento_de("1099887700", 2025) == vencimiento_de("1099887799", 2025)
    assert vencimiento_de("1099887700", 2025).month == 10


def test_el_ultimo_vencimiento_del_ag2025_es_en_octubre_de_2026() -> None:
    ultimo = vencimiento_de("1099887700", 2025)
    primero = vencimiento_de("1099887701", 2025)
    assert primero == date(2026, 8, 12), "01 y 02 vencen el 7.º día hábil de agosto de 2026"
    assert ultimo == date(2026, 10, 26), "99 y 00 vencen el 17.º día hábil de octubre de 2026"
    assert es_habil(primero) and es_habil(ultimo)


# ── el plazo cae en el año siguiente al gravable ──────────────────────────────────────────────


def test_el_plazo_es_del_anio_siguiente_al_gravable() -> None:
    """La declaración del año gravable 2025 se presenta en 2026. Confundirlo es un año de mora."""
    assert vencimiento_de("1099887701", 2025).year == 2026
    assert vencimiento_de("1099887701", 2026).year == 2027


def test_el_calendario_sirve_para_anios_futuros_sin_tocar_la_tabla() -> None:
    """El decreto es permanente ("y en adelante para cada año subsiguiente").

    Lo que cambia entre años son los festivos, no la tabla. Este test es el que prueba que el
    producto no envejece: sin él, alguien tendría que recordar actualizar fechas cada año.
    """
    for anio_gravable in (2025, 2026, 2027, 2030):
        fecha = vencimiento_de("1099887701", anio_gravable)
        assert fecha.year == anio_gravable + 1
        assert fecha.month == 8
        assert es_habil(fecha)


# ── el dígito de verificación ─────────────────────────────────────────────────────────────────────


def test_se_rechaza_el_nit_con_digito_de_verificacion() -> None:
    """El decreto dice "sin tener en cuenta el dígito de verificación".

    Recortarlo nosotros sería adivinar cuál dígito es el DV. Con el DV pegado, los dos últimos
    dígitos son otros y la fecha se corre: es exactamente el bug que este rechazo evita.
    """
    with pytest.raises(ValidationError, match="dígito de verificación"):
        vencimiento_de("1099887776-1", 2025)


def test_el_numero_se_limpia_de_puntos_y_espacios() -> None:
    """Una cédula escrita como la escribe una persona sigue sirviendo."""
    assert dos_ultimos_digitos("1.099.887.776") == 76
    assert dos_ultimos_digitos(" 1099887776 ") == 76


def test_un_documento_no_numerico_se_rechaza() -> None:
    with pytest.raises(ValidationError, match="solo dígitos"):
        vencimiento_de("abc123x", 2025)
