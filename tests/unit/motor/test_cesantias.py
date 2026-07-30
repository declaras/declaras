"""La exención de las cesantías y sus intereses (art. 206 num. 4 ET).

POR QUÉ ESTO EXISTÍA MAL: hasta esta versión el motor sumaba `cesantias_e_intereses` al bruto
laboral y nunca aplicaba la exención, así que TODO asalariado con cesantías pagaba impuesto de más.
Es el error en la dirección "segura" (no subdeclara) pero es el que un contador sí atrapa, y le
quita al producto el argumento de que la cifra está bien.

LA TABLA, literal del Estatuto: exento el 100% si el ingreso mensual promedio de los últimos seis
meses de vinculación no pasa de 350 UVT; de ahí baja al 90, 80, 60, 40, 20 y 0 por ciento en tramos
de 60 UVT. Con la UVT de 2025 ($49.799) el primer corte son $17.429.650 al mes.

EL DATO SÍ ESTÁ EN LA EXÓGENA, aunque la primera versión de esto asumió que no. El formato 2276 lo
reporta como una fila más ("Valor ingreso laboral promedio de los últimos seis meses"), así que casi
siempre la exención entra sola. Cuando el empleador no lo reportó, el motor grava completo y avisa,
y ahí sí hay que pedirle la certificación. Esa asimetría es deliberada y está probada abajo.
"""

from __future__ import annotations

import pytest

from declaras.caso import CasoTributario, Contribuyente, Fuente, IngresoLaboral
from declaras.motor.elecciones import Elecciones
from declaras.motor.general import base_general, rlg_general
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)
UVT = P.uvt

# Un sueldo con holgura sobre el tramo del 0% del art. 241, para que la exención mueva impuesto.
SALARIO = 120_000_000
CESANTIAS = 10_000_000


def _laboral(promedio_mensual_6m: int | None, cesantias: int = CESANTIAS) -> IngresoLaboral:
    return IngresoLaboral(
        empleador_nit="900",
        empleador_nombre="ACME",
        salarios=SALARIO,
        cesantias_e_intereses=cesantias,
        promedio_mensual_6m=promedio_mensual_6m,
        aportes_salud=4_800_000,
        aportes_pension=4_800_000,
        fuente=FX,
    )


def _liquidar(*laborales: IngresoLaboral) -> Traza:
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="X"), laborales=list(laborales)
    )
    t = Traza()
    base_general(caso, P, t)
    rlg_general(caso, P, Elecciones(), t)
    return t


# ── la tabla del art. 206 num. 4, tramo por tramo ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("uvt_mes", "pct_esperado"),
    [
        (100, 1.0),  # muy por debajo del corte
        (349, 1.0),  # justo debajo de 350
        (350, 1.0),  # "no exceda de 350 UVT": el 350 exacto está exento del todo
        (351, 0.90),  # ya excede: entra al primer tramo
        (410, 0.90),  # "entre 350 y 410": el 410 cierra el tramo del 90%
        (411, 0.80),
        (470, 0.80),
        (471, 0.60),
        (530, 0.60),
        (531, 0.40),
        (590, 0.40),
        (591, 0.20),
        (650, 0.20),  # "de 650 UVT, el 0%": hasta 650 inclusive es 20%
        (651, 0.0),
        (2000, 0.0),  # un sueldo alto no exime nada
    ],
)
def test_cada_tramo_de_la_gradualidad_exime_lo_que_dice_la_norma(
    uvt_mes: int, pct_esperado: float
) -> None:
    t = _liquidar(_laboral(promedio_mensual_6m=uvt_mes * UVT))

    esperado = round(CESANTIAS * pct_esperado)
    assert t.nodos["EXENTA_CESANTIAS"].valor == esperado, (
        f"con un promedio de {uvt_mes} UVT/mes la parte no gravada debería ser el "
        f"{pct_esperado:.0%} de {CESANTIAS:,}"
    )


def test_los_limites_de_tramo_son_inclusivos_hacia_arriba() -> None:
    """El Estatuto dice "entre 350 UVT y 410 UVT, el 90%".

    Un `<` en vez de `<=` corre todos los tramos y cambia el impuesto de quien cae exactamente en
    un límite. Se fija aparte porque es el error clásico de transcribir una tabla de tramos.
    """
    en_el_limite = _liquidar(_laboral(promedio_mensual_6m=410 * UVT))
    justo_encima = _liquidar(_laboral(promedio_mensual_6m=410 * UVT + 1))

    assert en_el_limite.nodos["EXENTA_CESANTIAS"].valor == 9_000_000  # 90%
    assert justo_encima.nodos["EXENTA_CESANTIAS"].valor == 8_000_000  # 80%


# ── el dato que falta: gravar completo y decirlo ──────────────────────────────────────────────


def test_sin_el_promedio_salarial_las_cesantias_se_gravan_completas() -> None:
    """`None` no es cero: es "no se sabe", y ahí la única salida honesta es no eximir.

    Asumir la exención sin el soporte baja el impuesto sobre una afirmación que nadie hizo, y eso
    es inexactitud: sanción del 100% del mayor impuesto más intereses de mora.
    """
    t = _liquidar(_laboral(promedio_mensual_6m=None))

    assert t.nodos["EXENTA_CESANTIAS"].valor == 0


def test_sin_el_promedio_queda_un_aviso_con_la_plata_en_juego() -> None:
    """Gravar de más en silencio le cuesta al cliente sin que sepa que hay algo que hacer."""
    t = _liquidar(_laboral(promedio_mensual_6m=None))

    [aviso] = [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]
    assert "ACME" in aviso.mensaje, "hay que decir de qué empleador falta el certificado"
    assert "10,000,000" in aviso.mensaje or "10.000.000" in aviso.mensaje
    assert "206" in aviso.mensaje, "con la norma, para que el contador la pueda verificar"


def test_con_el_promedio_no_hay_aviso() -> None:
    t = _liquidar(_laboral(promedio_mensual_6m=200 * UVT))

    assert not [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]


def test_sin_cesantias_no_hay_nodo_en_cero_sin_explicacion_ni_aviso() -> None:
    """El nodo existe siempre (la memoria de cálculo lo recorre) pero no inventa un pendiente."""
    t = _liquidar(_laboral(promedio_mensual_6m=None, cesantias=0))

    assert t.nodos["EXENTA_CESANTIAS"].valor == 0
    assert "sin cesantías" in t.nodos["EXENTA_CESANTIAS"].formula
    assert not [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]


# ── el promedio es por vínculo, no del año ────────────────────────────────────────────────────────


def test_el_promedio_se_evalua_por_cada_vinculo_laboral() -> None:
    """La norma dice "los seis últimos meses de vinculación", o sea de CADA vinculación.

    Dos empleadores con promedios distintos dan porcentajes distintos sobre sus propias cesantías.
    Promediarlos juntos le daría a uno el tramo del otro.
    """
    bajo = _laboral(promedio_mensual_6m=100 * UVT, cesantias=4_000_000)
    alto = IngresoLaboral(
        empleador_nit="901",
        empleador_nombre="OTRA SAS",
        salarios=SALARIO,
        cesantias_e_intereses=6_000_000,
        promedio_mensual_6m=700 * UVT,  # por encima de 650: 0%
        aportes_salud=0,
        aportes_pension=0,
        fuente=FX,
    )

    t = _liquidar(bajo, alto)

    assert t.nodos["EXENTA_CESANTIAS"].valor == 4_000_000, (
        "las del vínculo de sueldo bajo quedan 100% exentas y las del alto 0%"
    )


def test_un_vinculo_sin_dato_no_tumba_la_exencion_del_que_si_lo_tiene() -> None:
    """Lo que falta de uno no puede castigar al otro, y el aviso nombra solo al que falta."""
    con_dato = _laboral(promedio_mensual_6m=100 * UVT, cesantias=4_000_000)
    sin_dato = IngresoLaboral(
        empleador_nit="901",
        empleador_nombre="OTRA SAS",
        salarios=SALARIO,
        cesantias_e_intereses=6_000_000,
        promedio_mensual_6m=None,
        aportes_salud=0,
        aportes_pension=0,
        fuente=FX,
    )

    t = _liquidar(con_dato, sin_dato)

    assert t.nodos["EXENTA_CESANTIAS"].valor == 4_000_000
    [aviso] = [f for f in t.flags if f.codigo == "CESANTIAS_SIN_PROMEDIO_SALARIAL"]
    assert "OTRA SAS" in aviso.mensaje
    assert "ACME" not in aviso.mensaje, "no se puede pedir un certificado que ya está"


# ── el límite del 40% del art. 336 ────────────────────────────────────────────────────────────


def test_la_exencion_de_cesantias_entra_al_limite_del_cuarenta_por_ciento() -> None:
    """Verificado contra el art. 336 num. 3: solo exceptúa los 72 UVT y el 1% de facturas.

    Si esta exención quedara FUERA del cap, bajaría el impuesto más de lo que la ley permite, y esa
    es la dirección peligrosa. El test lo prueba mirando que APLICADO_40 nunca pase el cap.
    """
    t = _liquidar(_laboral(promedio_mensual_6m=100 * UVT))

    cap = t.nodos["CAP_40"].valor
    aplicado = t.nodos["APLICADO_40"].valor
    suma_sin_limite = t.nodos["DEDUCCIONES_LIMITADAS"].valor + t.nodos["EXENTA_25"].valor
    suma_sin_limite += t.nodos["EXENTA_CESANTIAS"].valor

    assert aplicado <= cap
    assert aplicado == min(suma_sin_limite, cap)
    assert "EXENTA_CESANTIAS" in t.nodos["APLICADO_40"].insumos, (
        "el nodo tiene que quedar declarado como insumo o la memoria de cálculo no lo explica"
    )


def test_la_exencion_baja_el_impuesto_de_verdad() -> None:
    """La prueba de que sirve: el mismo caso con y sin el certificado del empleador."""
    con = _liquidar(_laboral(promedio_mensual_6m=100 * UVT))
    sin = _liquidar(_laboral(promedio_mensual_6m=None))

    assert con.nodos["RLG_GENERAL"].valor < sin.nodos["RLG_GENERAL"].valor, (
        "conseguir el certificado tiene que bajar la base gravable, o el beneficio no existe"
    )
