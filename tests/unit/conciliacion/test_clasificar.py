"""Clasificar un ingreso que el motor no sabe ubicar, en vez de sacarlo de la liquidación.

EL PROBLEMA QUE RESUELVE: la exógena reporta servicios, honorarios y "otros" con conceptos que el
caso no modela (no hay cédula de independientes). La única salida era LLEVAR_A_MANO, que excluye el
ingreso con aviso bloqueante y deja al contador sumándolo aparte. En un caso real eran $5.623.876
sobre $69,5M, el 8% de los ingresos, más una retención de $254.400 que sí se reclamaba: pedir
crédito de una retención sin declarar la renta es la señal de auditoría más obvia que existe.

`Decision.CLASIFICAR` dice a qué cédula pertenece el ingreso y lo mete por ahí.

LA CLASE CAMBIA EL IMPUESTO, Y ESO GOBIERNA TODO EL DISEÑO. Rentas de trabajo da acceso al 25%
exento del art. 206 num. 10; rentas de capital no. Así que la clase no se elige "porque suena": se
deriva del hecho que el motivo afirma, y ese hecho queda escrito en un aviso que acompaña la
liquidación. Si alguien pudiera elegir la clase libremente, tendría un botón para bajar el impuesto.
"""

import pytest

from declaras.services.conciliacion import (
    ClaseDeIngreso,
    Concepto,
    Decision,
    Motivo,
    a_caso,
    abrir,
    avisos,
    resolver,
)
from declaras.services.conciliacion.mapeo import (
    INGRESO_CLASIFICADO,
    INGRESO_EXCLUIDO,
    INGRESO_LLEVADO_A_MANO,
)
from tests.unit.conciliacion.test_cruce import _exogena, _fila

CONTRIB = {"contribuyente": None}
# 5002 es HONORARIOS y 5004 es SERVICIOS: los dos están fuera del modelo del motor.
HONORARIO = ("901222333", "5002", 10_000_000)
SERVICIO = ("901222444", "5004", 4_240_000)


def _partida(datos, **kwargs):
    [p] = abrir(_exogena(_fila(*datos, **kwargs)))
    return p


def _clasificar(datos, clase, motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS):
    return resolver(
        _partida(datos), Decision.CLASIFICAR, motivo=motivo, quien="contador@x.co", clase=clase
    )


def _caso(partidas):
    from declaras.caso import Contribuyente

    return a_caso(
        partidas,
        contribuyente=Contribuyente(num_doc="1234567", nombre="Prueba"),
        anio_gravable=2025,
    )


# ── que el ingreso ENTRE, que es todo el punto ────────────────────────────────────────────────────


def test_un_honorario_clasificado_entra_al_caso_con_su_valor() -> None:
    """EL BUG QUE COMETÍ Y QUE ESTE TEST FIJA.

    `CLASIFICAR` no estaba en `DECISIONES_CON_HECHO`, así que el ingreso caía en la rama de
    "decisión sin hecho" y salía del caso con un aviso de EXCLUSIÓN. Visto desde afuera: el
    bloqueante desaparecía (ya no era LLEVAR_A_MANO), el renglón se mostraba resuelto, y el 210 no
    tenía la plata. Lo único que evitó que fuera silencioso es que la arquitectura exige aviso para
    toda exclusión, así que quedó registro — pero era el aviso equivocado.
    """
    clasificada = _clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO)
    caso = _caso([clasificada])

    assert caso.ingresos_brutos_totales == 10_000_000, (
        "el ingreso clasificado tiene que ENTRAR: si no, la clasificación solo apaga el bloqueante "
        "y deja el 210 incompleto con apariencia de resuelto"
    )
    assert len(caso.laborales) == 1
    assert caso.laborales[0].bruto == 10_000_000


def test_clasificar_apaga_el_bloqueante_y_deja_un_aviso_de_lo_que_se_afirmo() -> None:
    """El bloqueante existe porque el ingreso está por fuera. Si entra, sobra."""
    clasificada = _clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO)
    resultado = avisos([clasificada])

    codigos = {f.codigo for f in resultado}
    assert INGRESO_LLEVADO_A_MANO not in codigos
    assert INGRESO_EXCLUIDO not in codigos, "clasificar no es excluir"
    assert INGRESO_CLASIFICADO in codigos
    [aviso] = [f for f in resultado if f.codigo == INGRESO_CLASIFICADO]
    assert aviso.severidad == "advertencia", (
        "no puede ser bloqueante (el ingreso sí entró) ni informativo (el supuesto legal hay que "
        "poder revisarlo antes de presentar)"
    )


def test_el_aviso_dice_bajo_que_norma_entro_el_ingreso() -> None:
    """Es la defensa de la declaración: por qué ese ingreso está en esa cédula."""
    clasificada = _clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO)
    [aviso] = [f for f in avisos([clasificada]) if f.codigo == INGRESO_CLASIFICADO]

    assert "206" in aviso.mensaje and "336" in aviso.mensaje, (
        "sin el artículo, el aviso no sirve para sostener la clasificación ante la DIAN"
    )
    assert "$10.000.000" in aviso.mensaje, "tiene que decir la cifra que se movió"
    assert "dos o más trabajadores" in aviso.mensaje, (
        "tiene que decir el HECHO del que depende, porque si no se cumple la cédula es otra"
    )


@pytest.mark.parametrize(
    ("clase", "lista"),
    [
        (ClaseDeIngreso.RENTA_DE_TRABAJO, "laborales"),
        (ClaseDeIngreso.RENDIMIENTO, "rendimientos"),
        (ClaseDeIngreso.ARRIENDO, "arriendos"),
    ],
)
def test_cada_clase_entra_en_la_lista_que_le_corresponde(clase: ClaseDeIngreso, lista: str) -> None:
    motivo = (
        Motivo.SIN_COSTOS_NI_EMPLEADOS
        if clase is ClaseDeIngreso.RENTA_DE_TRABAJO
        else Motivo.NATURALEZA_DEL_INGRESO
    )
    caso = _caso([_clasificar(SERVICIO, clase, motivo)])

    assert len(getattr(caso, lista)) == 1
    assert caso.ingresos_brutos_totales == 4_240_000


def test_los_aportes_entran_en_cero_y_no_inventados() -> None:
    """La exógena reporta lo que el tercero PAGÓ, no lo que el independiente aportó.

    Estimar un 12,5% + 16% sobre un IBC presunto fabricaría una deducción sin soporte. El
    certificado de seguridad social se pide aparte, como cualquier otro beneficio invisible.
    """
    caso = _caso([_clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO)])

    [laboral] = caso.laborales
    assert laboral.aportes_salud == 0
    assert laboral.aportes_pension == 0


# ── las guardas: por qué no es un botón para bajar el impuesto ────────────────────────────────────


def test_no_se_puede_reclasificar_un_ingreso_que_el_motor_ya_ubica() -> None:
    """Sin esta guarda, CLASIFICAR sería un botón para mover un salario a la cédula que convenga."""
    salario = _partida(("900111222", "5001", 87_400_000))

    with pytest.raises(ValueError, match="ya tiene su cédula"):
        resolver(
            salario,
            Decision.CLASIFICAR,
            motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS,
            quien="contador@x.co",
            clase=ClaseDeIngreso.RENTA_DE_TRABAJO,
        )


def test_renta_de_trabajo_exige_el_hecho_que_habilita_el_25_por_ciento() -> None:
    """El art. 206 num. 10 depende de no imputar costos ni tener dos o más trabajadores.

    Con `NATURALEZA_DEL_INGRESO` se está afirmando otra cosa ("esto en realidad es un arriendo"),
    que no habilita el 25%. Aceptarlo dejaría en el registro una clasificación cuyo motivo no la
    sostiene, que es justo lo que un auditor busca.
    """
    with pytest.raises(ValueError, match="no se sostiene con el motivo"):
        _clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO, Motivo.NATURALEZA_DEL_INGRESO)


def test_clasificar_sin_clase_se_rechaza() -> None:
    with pytest.raises(ValueError, match="necesita la clase"):
        resolver(
            _partida(HONORARIO),
            Decision.CLASIFICAR,
            motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS,
            quien="contador@x.co",
        )


def test_la_clase_sin_clasificar_se_rechaza() -> None:
    """Una clase colgada de otra decisión se ignoraría en silencio, y eso pierde una intención."""
    with pytest.raises(ValueError, match="solo tiene sentido con la decisión CLASIFICAR"):
        resolver(
            _partida(HONORARIO),
            Decision.MARCAR_AJENO,
            motivo=Motivo.NO_ES_MIO,
            quien="contador@x.co",
            clase=ClaseDeIngreso.RENTA_DE_TRABAJO,
        )


def test_clasificar_toma_la_cifra_de_la_version_y_no_cero() -> None:
    """Cae en el mismo `_derivar_valor` que MARCAR_AJENO, que devuelve 0.

    Un CLASIFICAR valiendo cero metería el ingreso a su cédula en cero: subdeclarar con la
    apariencia de haberlo resuelto, que es peor que el bloqueante que vino a reemplazar.
    """
    clasificada = _clasificar(HONORARIO, ClaseDeIngreso.RENTA_DE_TRABAJO)

    assert clasificada.resolucion is not None
    assert clasificada.resolucion.valor == 10_000_000


def test_se_puede_clasificar_con_una_cifra_distinta_a_la_reportada() -> None:
    """Sobre una discrepancia hay que poder decir cuál cifra rige Y a qué cédula va.

    Una partida tiene UNA resolución, así que sin esto clasificar un renglón con dos versiones
    distintas era un callejón: había que elegir entre arreglar la cifra o ubicar el ingreso.
    """
    clasificada = resolver(
        _partida(HONORARIO),
        Decision.CLASIFICAR,
        motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS,
        quien="contador@x.co",
        clase=ClaseDeIngreso.RENTA_DE_TRABAJO,
        valor=9_500_000,
    )

    assert clasificada.resolucion is not None
    assert clasificada.resolucion.valor == 9_500_000
    assert _caso([clasificada]).ingresos_brutos_totales == 9_500_000


def test_la_clase_queda_en_la_resolucion_para_poder_auditarla() -> None:
    clasificada = _clasificar(SERVICIO, ClaseDeIngreso.ARRIENDO, Motivo.NATURALEZA_DEL_INGRESO)

    assert clasificada.resolucion is not None
    assert clasificada.resolucion.clase is ClaseDeIngreso.ARRIENDO
    assert clasificada.resolucion.motivo is Motivo.NATURALEZA_DEL_INGRESO


def test_el_concepto_sigue_siendo_el_que_reporto_la_exogena() -> None:
    """Clasificar dice a qué cédula VA, no reescribe lo que el tercero reportó.

    Si el concepto cambiara, se perdería la trazabilidad contra la exógena y un auditor no podría
    reconstruir de dónde salió el renglón.
    """
    clasificada = _clasificar(SERVICIO, ClaseDeIngreso.RENTA_DE_TRABAJO)

    assert clasificada.concepto is Concepto.SERVICIOS


# ── la retención: la mitad de la plata que estaba en juego ────────────────────────────────────


def test_la_retencion_del_tercero_viaja_con_el_ingreso_clasificado() -> None:
    """MEDIDO EN EL CASO REAL: $254.400 de retención sin ingreso al que colgarse.

    El único ingreso de ese tercero era el clasificado, y los clasificados se ensamblan
    después del loop por concepto, cuando la retención pendiente ya no se le ofrecía a nadie. El
    ingreso entraba a la declaración y su retención no: la renta sumaba al impuesto y el crédito
    que la compensa se quedaba por fuera. Al arreglarlo el saldo pasó de $0 a −$254.400, o sea de
    no devolverle nada a devolverle esa plata.
    """
    from tests.unit.conciliacion.fabricas import fila_retencion

    partidas = abrir(_exogena(_fila(*SERVICIO), fila_retencion(SERVICIO[0], 254_400)))
    resueltas = [
        resolver(
            p,
            Decision.CLASIFICAR,
            motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS,
            quien="contador@x.co",
            clase=ClaseDeIngreso.RENTA_DE_TRABAJO,
        )
        if p.concepto is Concepto.SERVICIOS
        else resolver(p, Decision.USAR_DIAN, motivo=Motivo.COINCIDEN, quien="contador@x.co")
        if p.estado.value == "COINCIDE"
        else resolver(
            p, Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )
        for p in partidas
    ]
    caso = _caso(resueltas)

    [laboral] = caso.laborales
    assert laboral.retencion == 254_400, (
        "la retención que el mismo tercero reportó tiene que colgarse del ingreso clasificado: "
        "declarar la renta sin su retención le cuesta plata al cliente"
    )


def test_la_retencion_no_se_cuenta_dos_veces_si_hay_otro_ingreso_del_mismo_tercero() -> None:
    """Un tercero con un salario Y un servicio: la retención es UNA y va a un solo ingreso.

    Duplicarla fabricaría un saldo a favor que no existe, que es el error más caro en la otra
    dirección: declarar más retención que lo reportado casi garantiza requerimiento.
    """
    from tests.unit.conciliacion.fabricas import fila_retencion

    nit = "900111222"
    partidas = abrir(
        _exogena(
            _fila(nit, "5001", 50_000_000),
            _fila(nit, "5004", 4_000_000),
            fila_retencion(nit, 300_000),
        )
    )
    resueltas = [
        resolver(
            p,
            Decision.CLASIFICAR,
            motivo=Motivo.SIN_COSTOS_NI_EMPLEADOS,
            quien="contador@x.co",
            clase=ClaseDeIngreso.RENTA_DE_TRABAJO,
        )
        if p.concepto is Concepto.SERVICIOS
        else resolver(
            p, Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )
        for p in partidas
    ]
    caso = _caso(resueltas)

    total = sum(x.retencion for x in caso.laborales)
    assert total == 300_000, (
        f"la retención se contó {total / 300_000:.0f} veces: es un solo hecho y duplicarla "
        "fabrica un saldo a favor sin sustento"
    )
