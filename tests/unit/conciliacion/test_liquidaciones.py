"""La liquidación conciliada: la ÚNICA puerta por la que las partidas se vuelven un 210.

Dos cosas se prueban acá y ninguna es cosmética. Una: los avisos del conciliador se
fusionan en `Liquidacion.flags`, que es el único canal por el que seis códigos llegan
impresos al borrador (el motor está congelado y no puede levantarlos, y `CasoTributario`
no tiene dónde llevarlos). Sin la fusión, un 210 al que le falta un ingreso no se ve
incompleto. Dos: `bloqueante` bloquea de verdad — no es una etiqueta que solo se pinta.
"""

from datetime import UTC, datetime

import pytest

from declaras.caso import (
    Beneficios,
    CasoTributario,
    Contribuyente,
    Dependiente,
    Fuente,
    IngresoLaboral,
)
from declaras.motor import Elecciones, Flag, liquidar
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render import memoria_markdown
from declaras.services.conciliacion import (
    INGRESO_LLEVADO_A_MANO,
    PENSION_DISTRIBUIDA_UNIFORME,
    Decision,
    Motivo,
    a_caso,
    autorresolver,
    resolver,
)
from declaras.services.conciliacion.liquidaciones import (
    SEVERIDAD_BLOQUEANTE,
    LiquidacionVersionada,
    bloqueantes,
    ganancia,
    hay_bloqueante,
    liquidar_conciliado,
    liquidar_y_versionar,
)
from tests.unit.conciliacion.fabricas import partida_honorarios, partida_pension

P = cargar(2025)
CONTRIB = Contribuyente(num_doc="1234567", nombre="Prueba")
AHORA = datetime(2026, 7, 27, tzinfo=UTC)
FUENTE = Fuente.manual("prueba")


def _caso(salario: int = 120_000_000, *, con_dependiente: bool = True) -> CasoTributario:
    """Un asalariado con un dependiente: a 120 millones el óptimo (387 + 72 UVT) NO es la
    elección por defecto del modelo, así que "no se optimizó" es observable."""
    dependientes = [Dependiente(tipo="hijo_menor", fuente=FUENTE)] if con_dependiente else []
    return CasoTributario(
        anio_gravable=2025,
        contribuyente=CONTRIB,
        laborales=[
            IngresoLaboral(
                empleador_nit="900111222",
                empleador_nombre="ACME SAS",
                salarios=salario,
                aportes_salud=0,
                aportes_pension=0,
                fuente=FUENTE,
            )
        ],
        beneficios=Beneficios(dependientes=dependientes),
    )


def _llevada_a_mano():
    return resolver(
        partida_honorarios(),
        Decision.LLEVAR_A_MANO,
        motivo=Motivo.FUERA_DEL_MOTOR,
        quien="contador",
    )


# ─────────────────────────── la fusión de avisos ───────────────────────────


def test_los_avisos_del_conciliador_entran_a_la_liquidacion():
    """Es el requisito heredado de T5: sin esto los seis códigos no existen para nadie."""
    partidas = autorresolver([partida_pension()])
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)
    liq = liquidar_conciliado(caso, partidas, P)
    assert liq.tiene_flag(PENSION_DISTRIBUIDA_UNIFORME)


def test_el_aviso_fusionado_sale_impreso_en_la_memoria():
    """El borrador y la memoria imprimen `Liquidacion.flags`: si el aviso no se fusiona,
    el contador no lo ve en ninguna parte."""
    partidas = autorresolver([partida_pension()])
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)
    texto = memoria_markdown(liquidar_conciliado(caso, partidas, P), caso)
    assert PENSION_DISTRIBUIDA_UNIFORME in texto


def test_la_fusion_no_pisa_los_flags_del_motor():
    """El motor levanta los suyos (el componente inflacionario provisional, la confianza
    baja): la fusión SUMA, no reemplaza."""
    partidas = autorresolver([partida_pension()])
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)
    del_motor = liquidar(caso, P, Elecciones()).flags
    liq = liquidar_conciliado(caso, partidas, P)
    assert [f.codigo for f in del_motor]  # el motor sí tiene algo que decir de este caso
    assert {f.codigo for f in del_motor} <= {f.codigo for f in liq.flags}


def test_el_ingreso_llevado_a_mano_llega_como_bloqueante():
    """El aviso que dice "este ingreso quedó por fuera": si no llega, un 210 incompleto
    se ve completo."""
    partidas = [*autorresolver([partida_pension()]), _llevada_a_mano()]
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)
    liq = liquidar_conciliado(caso, partidas, P)
    codigos = [f.codigo for f in bloqueantes(liq)]
    assert codigos == [INGRESO_LLEVADO_A_MANO]
    assert "ZETA SAS" in next(f.mensaje for f in liq.flags if f.codigo == INGRESO_LLEVADO_A_MANO)


# ─────────────────────────── que bloqueante bloquee ───────────────────────────


def test_sin_bloqueante_se_optimiza():
    """El control del caso de abajo: acá el óptimo sí se elige."""
    liq = liquidar_conciliado(_caso(), [], P)
    assert liq.elecciones == optimizar(_caso(), P).elecciones
    assert liq.elecciones.usar_387 is True


def test_no_se_optimiza_sobre_una_liquidacion_bloqueada():
    """La elección de menor impuesto calculada sobre una base a la que le FALTA un ingreso
    puede ser la equivocada para el 210 completo, y el contador que suma ese ingreso a
    mano se quedaría con la elección mala. Se liquida con las elecciones por defecto."""
    liq = liquidar_conciliado(_caso(), [_llevada_a_mano()], P)
    assert liq.elecciones == Elecciones()
    assert liq.elecciones.usar_387 is False
    assert liq.valor("IMPUESTO_NETO") > optimizar(_caso(), P).liquidacion.valor("IMPUESTO_NETO")


def test_la_liquidacion_bloqueada_igual_se_puede_ver():
    """Bloquear no puede ser esconder: el borrador tiene que salir CON el aviso, porque
    es el único sitio donde el contador lee qué le falta."""
    liq = liquidar_conciliado(_caso(), [_llevada_a_mano()], P)
    assert liq.tiene_flag(INGRESO_LLEVADO_A_MANO)
    assert memoria_markdown(liq, _caso()).count(INGRESO_LLEVADO_A_MANO) == 1


def test_optimizar_se_niega_con_un_bloqueante_previo():
    """El camino paralelo: quien llame al optimizador directamente pasándole los avisos
    del conciliador tampoco recibe un óptimo. Hoy el motor no emite bloqueantes, así que
    esta es la única forma de llegarle."""
    bloqueante = Flag(codigo="X", mensaje="Falta un ingreso.", severidad=SEVERIDAD_BLOQUEANTE)
    with pytest.raises(ValueError, match="bloqueantes"):
        optimizar(_caso(), P, flags_previos=[bloqueante])


def test_optimizar_ignora_las_advertencias():
    """Una advertencia no bloquea: si lo hiciera, ningún caso con componente
    inflacionario provisional se podría optimizar."""
    aviso = Flag(codigo="X", mensaje="Revisar esto.", severidad="advertencia")
    assert optimizar(_caso(), P, flags_previos=[aviso]).elecciones.usar_387 is True


def test_hay_bloqueante_solo_mira_la_severidad():
    assert hay_bloqueante([Flag(codigo="A", mensaje="M.", severidad=SEVERIDAD_BLOQUEANTE)])
    assert not hay_bloqueante([Flag(codigo="A", mensaje="M.", severidad="advertencia")])
    assert not hay_bloqueante([Flag(codigo="A", mensaje="M.", severidad="info")])
    assert not hay_bloqueante([])


# ─────────────────────────── versiones y ganancia ───────────────────────────


def test_la_version_lleva_su_momento_y_su_liquidacion():
    v = liquidar_y_versionar(_caso(), [], p=P, version=1, momento=AHORA)
    assert v.version == 1
    assert v.momento == AHORA
    assert v.impuesto == v.liquidacion.valor("IMPUESTO_NETO")
    assert v.saldo == v.liquidacion.valor("SALDO")


def test_la_version_sobrevive_dump_y_validate():
    """Es lo que la persistencia hace con ella; si no sobrevive, el preliminar guardado
    no se puede volver a leer."""
    v = liquidar_y_versionar(_caso(), [_llevada_a_mano()], p=P, version=2, momento=AHORA)
    vuelta = LiquidacionVersionada.model_validate(v.model_dump(mode="json"))
    assert vuelta == v
    assert vuelta.liquidacion.tiene_flag(INGRESO_LLEVADO_A_MANO)


def test_la_ganancia_es_lo_que_bajo_el_impuesto():
    preliminar = liquidar_y_versionar(_caso(), [], p=P, version=1, momento=AHORA)
    # El 220 trae los aportes obligatorios, que son INCRNGO: el impuesto baja.
    con_aportes = _caso().model_copy(
        update={
            "laborales": [
                _caso()
                .laborales[0]
                .model_copy(update={"aportes_salud": 4_800_000, "aportes_pension": 4_800_000})
            ]
        }
    )
    actual = liquidar_y_versionar(con_aportes, [], p=P, version=2, momento=AHORA)
    assert actual.impuesto < preliminar.impuesto
    assert ganancia(preliminar, actual) == preliminar.impuesto - actual.impuesto
    assert ganancia(preliminar, actual) > 0


def test_la_ganancia_de_una_sola_version_es_cero():
    """Justo después de conciliar, preliminar y actual son la misma: no hay ganancia que
    presumir todavía."""
    v = liquidar_y_versionar(_caso(), [], p=P, version=1, momento=AHORA)
    assert ganancia(v, v) == 0
