"""Festivos y días hábiles de Colombia.

Esto es la base del calendario de vencimientos, así que un error acá le da a un cliente la fecha
equivocada y le cuesta la sanción mínima ($524.000 en 2026). Los festivos NO se tabulan a mano: se
calculan, porque una tabla que le falte un año no da error, da el festivo equivocado.
"""

from __future__ import annotations

from datetime import date

import pytest

from declaras.calendario import (
    dia_habil_del_mes,
    domingo_de_pascua,
    es_habil,
    festivos,
)

# Domingos de Pascua publicados, para verificar el algoritmo de Meeus/Butcher.
PASCUAS = {
    2023: date(2023, 4, 9),
    2024: date(2024, 3, 31),
    2025: date(2025, 4, 20),
    2026: date(2026, 4, 5),
    2027: date(2027, 3, 28),
    2028: date(2028, 4, 16),
    2030: date(2030, 4, 21),
}


@pytest.mark.parametrize(("anio", "esperado"), sorted(PASCUAS.items()))
def test_el_domingo_de_pascua_coincide_con_el_publicado(anio: int, esperado: date) -> None:
    assert domingo_de_pascua(anio) == esperado
    assert domingo_de_pascua(anio).weekday() == 6, "la Pascua es un domingo, siempre"


# Los festivos de dos años completos, verificados uno por uno contra el calendario oficial. Se fijan
# las FECHAS y no el conteo: el número de festivos varía entre años porque dos pueden caer el mismo
# día, así que un conteo fijo prueba menos y falla por la razón equivocada.
FESTIVOS_2026 = (
    date(2026, 1, 1),  # Año Nuevo
    date(2026, 1, 12),  # Reyes, del martes 6 al lunes 12
    date(2026, 3, 23),  # San José, del jueves 19 al lunes 23
    date(2026, 4, 2),  # Jueves Santo
    date(2026, 4, 3),  # Viernes Santo
    date(2026, 5, 1),  # Día del Trabajo
    date(2026, 5, 18),  # Ascensión
    date(2026, 6, 8),  # Corpus Christi
    date(2026, 6, 15),  # Sagrado Corazón
    date(2026, 6, 29),  # San Pedro y San Pablo, ya es lunes
    date(2026, 7, 20),  # Independencia
    date(2026, 8, 7),  # Batalla de Boyacá
    date(2026, 8, 17),  # Asunción, del sábado 15 al lunes 17
    date(2026, 10, 12),  # Día de la Raza, ya es lunes
    date(2026, 11, 2),  # Todos los Santos, del domingo 1 al lunes 2
    date(2026, 11, 16),  # Independencia de Cartagena, del miércoles 11 al lunes 16
    date(2026, 12, 8),  # Inmaculada Concepción
    date(2026, 12, 25),  # Navidad
)


def test_los_festivos_de_2026_son_exactamente_los_publicados() -> None:
    """2026 es el año en que vence el AG2025, así que este es el año que importa hoy."""
    assert festivos(2026) == frozenset(FESTIVOS_2026)


def test_dos_festivos_pueden_caer_el_mismo_dia_y_el_ano_queda_con_uno_menos() -> None:
    """2025: San Pedro cae domingo 29 de junio y se traslada al lunes 30, que ya es Sagrado Corazón.

    No hay traslado en cascada en la ley, así que ese año Colombia tuvo 17 días festivos y no 18.
    Por eso `festivos()` devuelve un conjunto de FECHAS y no una lista de nombres: la pregunta que
    hay que responder es "¿este día es hábil?", y para eso el día repetido no aporta.
    """
    assert date(2025, 6, 30) in festivos(2025)
    assert len(festivos(2025)) == 17
    assert len(festivos(2026)) == 18


def test_los_festivos_fijos_no_se_trasladan_aunque_caigan_en_fin_de_semana() -> None:
    """El 7 de agosto de 2026 es viernes y el 20 de julio de 2025 es domingo. Ninguno se corre."""
    assert date(2026, 8, 7) in festivos(2026)
    assert date(2025, 7, 20) in festivos(2025)
    assert date(2025, 7, 21) not in festivos(2025), "Independencia no se traslada al lunes"


def test_los_trasladables_se_corren_al_lunes_siguiente() -> None:
    """Ley 51 de 1983. La Asunción de 2026 cae sábado 15 y se corre al lunes 17."""
    assert date(2026, 8, 15) not in festivos(2026)
    assert date(2026, 8, 17) in festivos(2026)


def test_un_trasladable_que_ya_cae_lunes_se_queda_donde_esta() -> None:
    """El Día de la Raza de 2026 es lunes 12 de octubre: no se corre al 19.

    Es el caso que un `+7` mal escrito rompe, y rompería el vencimiento de la mitad de los
    contribuyentes de octubre.
    """
    assert date(2026, 10, 12).weekday() == 0
    assert date(2026, 10, 12) in festivos(2026)
    assert date(2026, 10, 19) not in festivos(2026)


def test_la_semana_santa_no_se_traslada() -> None:
    """Jueves y Viernes Santo caen donde caen; los otros derivados de la Pascua sí se corren."""
    pascua = domingo_de_pascua(2026)
    assert pascua.replace(day=pascua.day - 3) in festivos(2026), "Jueves Santo"
    assert pascua.replace(day=pascua.day - 2) in festivos(2026), "Viernes Santo"


@pytest.mark.parametrize(
    ("dia", "habil"),
    [
        (date(2026, 8, 6), True),  # jueves común
        (date(2026, 8, 7), False),  # Batalla de Boyacá
        (date(2026, 8, 8), False),  # sábado
        (date(2026, 8, 9), False),  # domingo
        (date(2026, 8, 17), False),  # Asunción trasladada
        (date(2026, 8, 18), True),  # martes común
    ],
)
def test_es_habil_distingue_fin_de_semana_y_festivo(dia: date, habil: bool) -> None:
    assert es_habil(dia) is habil


def test_el_sabado_no_es_habil() -> None:
    """Las entidades autorizadas para recaudar no operan el fin de semana.

    Contarlo como hábil correría todos los vencimientos hacia atrás.
    """
    assert not es_habil(date(2026, 8, 1))


def test_el_septimo_dia_habil_de_agosto_de_2026_es_el_doce() -> None:
    """Contado a mano: 3, 4, 5 y 6 (el 7 es festivo), 10, 11 y 12."""
    assert dia_habil_del_mes(2026, 8, 7) == date(2026, 8, 12)


def test_agosto_de_2026_tiene_exactamente_los_diecinueve_habiles_que_la_tabla_necesita() -> None:
    """La tabla del decreto pide hasta el 19.º día hábil de agosto, así que el margen es CERO.

    Si un año agosto tuviera 18 días hábiles, algún par de dígitos no tendría fecha. Por eso
    `dia_habil_del_mes` falla en vez de devolver el último día del mes: es el aviso de que hay que
    revisar los festivos.
    """
    assert dia_habil_del_mes(2026, 8, 19) == date(2026, 8, 31)
    with pytest.raises(ValueError, match="solo tiene 19 días hábiles"):
        dia_habil_del_mes(2026, 8, 20)


def test_pedir_un_dia_habil_que_no_existe_falla_en_vez_de_devolver_el_ultimo() -> None:
    """Devolver una fecha cercana volvería el error indetectable."""
    with pytest.raises(ValueError, match="días hábiles"):
        dia_habil_del_mes(2026, 2, 25)


def test_el_ordinal_empieza_en_uno() -> None:
    """Un cero sería el error de quien piense que es un índice, y daría el día equivocado."""
    with pytest.raises(ValueError, match="empieza en 1"):
        dia_habil_del_mes(2026, 8, 0)


def test_ningun_dia_habil_devuelto_es_festivo_ni_fin_de_semana() -> None:
    """Barrido de tres años sobre los meses del calendario de renta."""
    for anio in (2026, 2027, 2028):
        for mes in (8, 9, 10):
            for ordinal in range(1, 18):
                dia = dia_habil_del_mes(anio, mes, ordinal)
                assert dia.month == mes and dia.year == anio
                assert es_habil(dia)
