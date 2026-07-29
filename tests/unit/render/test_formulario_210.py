"""El 210 que se va a radicar: los nodos del motor puestos en las casillas oficiales.

POR QUE ESTE MODULO EXISTE

El borrador se presentaba "por conceptos" (RLG_GENERAL, IMPUESTO_241), que es la memoria del
cálculo y sirve para auditarlo, pero no es lo que se radica. Y el sistema mostraba en otro sitio
los renglones que la DIAN SUGIERE en la exógena, que son otra cosa: se midió la diferencia en un
caso real y la misma casilla traía dos cifras (R29 con $4.571.109 de diferencia, R32 con
$7.440.277). Las dos eran correctas y eran cosas distintas, pero nada lo decía.

LA GARANTIA DE QUE CUADRA son las identidades del propio formulario. El 210 se valida solo: el
patrimonio líquido es el bruto menos las deudas, la renta líquida es los ingresos menos lo no
constitutivo, la renta de la cédula general es la suma de sus cuatro columnas. Si el mapeo se
desincroniza, esas restas dejan de dar y estos casos fallan.
"""

from __future__ import annotations

import pytest

from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render.formulario import CASILLAS_DEL_210, formulario_210
from tests.golden.casos import g1, g2, g3

P = cargar(2025)


def _casillas(hacer) -> dict[int, int]:
    caso = hacer()
    return {c.numero: c.valor for c in formulario_210(optimizar(caso, P).liquidacion, caso)}


@pytest.mark.parametrize("hacer", [g1, g2, g3], ids=["g1", "g2", "g3"])
def test_el_patrimonio_liquido_es_el_bruto_menos_las_deudas(hacer):
    """Identidad del formulario: casilla 31 = 29 − 30, y nunca negativa."""
    c = _casillas(hacer)
    assert c[31] == max(c[29] - c[30], 0)


@pytest.mark.parametrize("hacer", [g1, g2, g3], ids=["g1", "g2", "g3"])
def test_la_renta_liquida_de_trabajo_es_los_ingresos_menos_lo_no_constitutivo(hacer):
    """Identidad del formulario: casilla 34 = 32 − 33."""
    c = _casillas(hacer)
    assert c[34] == max(c[32] - c[33], 0)


@pytest.mark.parametrize("hacer", [g1, g2, g3], ids=["g1", "g2", "g3"])
def test_la_renta_de_la_cedula_general_es_la_suma_de_sus_columnas(hacer):
    """Identidad del formulario: casilla 91 = 34 + 46 + 61 + 78, las cuatro columnas."""
    c = _casillas(hacer)
    assert c[91] == c[34] + c[46] + c[61] + c[78]


@pytest.mark.parametrize("hacer", [g1, g2, g3], ids=["g1", "g2", "g3"])
def test_la_renta_ordinaria_de_la_cedula_descuenta_lo_limitado(hacer):
    """Identidad del formulario: casilla 93 = 91 − 92."""
    c = _casillas(hacer)
    assert c[93] == max(c[91] - c[92], 0)


def test_las_cifras_del_formulario_son_las_mismas_del_motor():
    """El formulario NO recalcula nada: reparte lo que el motor ya liquidó. Si aquí saliera otra
    cifra, habría dos verdades sobre el mismo impuesto."""
    caso = g2()
    liq = optimizar(caso, P).liquidacion
    c = {x.numero: x.valor for x in formulario_210(liq, caso)}

    # RLG_GENERAL del motor es la renta GRAVABLE, que en el formulario es la 97 y no la 91: la 91
    # es la suma de las cuatro columnas ANTES de restar las exentas y deducciones limitadas. Esta
    # prueba apuntaba a la 91 y encontró que yo había confundido las dos.
    assert c[97] == liq.valor("RLG_GENERAL")
    assert c[93] == liq.valor("RLG_GENERAL"), (
        "la ordinaria y la gravable coinciden sin rentas gravables especiales"
    )
    assert c[126] == liq.valor("IMPUESTO_NETO")
    assert c[132] == liq.valor("RETENCIONES")
    assert c[133] == liq.valor("ANTICIPO_SIGUIENTE")


def test_el_saldo_va_a_pagar_o_a_favor_pero_nunca_a_las_dos():
    """El 210 tiene dos casillas y son excluyentes: 138 a pagar, 139 a favor. Poner cifra en las
    dos es un formulario que no cuadra."""
    for hacer in (g1, g2, g3):
        c = _casillas(hacer)
        assert not (c[138] and c[139]), f"{hacer.__name__} llenó las dos casillas de saldo"
        # Y la que se llene tiene que ser del tamaño del saldo que calculó el motor.
        saldo = optimizar(hacer(), P).liquidacion.valor("SALDO")
        assert c[138] == max(saldo, 0)
        assert c[139] == max(-saldo, 0)


def test_cada_casilla_lleva_su_nombre_oficial():
    """Nadie debería tener que saber qué es "la casilla 42": el nombre sale del formulario."""
    for c in formulario_210(optimizar(g2(), P).liquidacion, g2()):
        assert c.nombre, f"casilla {c.numero} sin nombre"
        assert not c.nombre.startswith("casilla"), f"casilla {c.numero} sin nombre oficial"


def test_todas_las_casillas_que_se_llenan_existen_en_el_formulario():
    """Una casilla inventada sería un renglón que la DIAN no tiene."""
    numeros = {c.numero for c in formulario_210(optimizar(g3(), P).liquidacion, g3())}
    assert numeros <= set(CASILLAS_DEL_210), numeros - set(CASILLAS_DEL_210)
