"""Las peticiones derivadas: qué documento falta, por qué, y en qué orden pedirlo.

Se derivan, no se almacenan: la lista sale de las partidas + lo que el cliente ya
contestó + el caso que hay hoy. Lo único persistido es la `Respuesta`, y un "no" apaga
la petición para siempre.
"""

from datetime import UTC, datetime

import pytest

from declaras.caso import Beneficios, CasoTributario, Contribuyente, Fuente, MontoDeclarado
from declaras.parametros import cargar
from declaras.services.conciliacion import (
    CONCEPTOS_FUERA_DEL_MOTOR,
    Concepto,
    Decision,
    EstadoPartida,
    Motivo,
    Respuesta,
    a_caso,
    abrir,
    autorresolver,
    resolver,
)
from declaras.services.conciliacion.peticiones import (
    MAXIMO_PETICIONES,
    UMBRAL_AHORRO,
    Peticion,
    derivar_peticiones,
)
from tests.unit.conciliacion.fabricas import fila_retencion, partida_honorarios
from tests.unit.conciliacion.test_cruce import _exogena, _fila

AHORA = datetime(2026, 7, 27, tzinfo=UTC)
CONTRIB = Contribuyente(num_doc="1234567", nombre="Prueba")
CASO = CasoTributario(anio_gravable=2025, contribuyente=CONTRIB)
P = cargar(2025)

# Varias partidas SOLO_DIAN de conceptos distintos, más las de beneficios invisibles que
# la lista siempre trae: sirve para probar el orden y los dos cortes.
PARTIDAS_VARIAS = abrir(
    _exogena(
        _fila("900111222", "5001", 87_400_000),
        _fila("890903938", "5010", 8_000_000, nombre="BANCO DEMO"),
        _fila("901555444", "5005", 24_000_000, nombre="ARRENDATARIO SAS"),
        fila_retencion("890903938", 560_000),
    )
)


def _caso_con_el_laboral() -> CasoTributario:
    """El caso que sale de la exógena sola: el salario ya está, los aportes no."""
    partidas = autorresolver(abrir(_exogena(_fila("900111222", "5001", 87_400_000))))
    return a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)


# ─────────────────────────── los cuatro casos del plan ───────────────────────────


def test_partida_solo_dian_genera_peticion_con_el_tercero():
    ps = derivar_peticiones(abrir(_exogena(_fila("900111222", "5001", 87_400_000))), [], CASO)
    assert any(p.tercero is not None and p.tercero["nit"] == "900111222" for p in ps)


def test_beneficio_invisible_pregunta_antes_de_pedir():
    ps = derivar_peticiones([], [], CASO)
    prepagada = next(p for p in ps if p.tipo_documento == "CERT_PREPAGADA")
    assert prepagada.pregunta_previa is not None


def test_respuesta_negativa_apaga_la_peticion_para_siempre():
    respuestas = [Respuesta(pregunta="PREPAGADA", tiene=False, detalle={}, quien="c", cuando=AHORA)]
    ps = derivar_peticiones([], respuestas, CASO)
    assert not any(p.tipo_documento == "CERT_PREPAGADA" for p in ps)


def test_se_ordenan_por_ahorro_y_se_corta_por_umbral():
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], CASO)
    assert [p.ahorro_estimado for p in ps] == sorted((p.ahorro_estimado for p in ps), reverse=True)
    assert all(p.ahorro_estimado >= UMBRAL_AHORRO or p.ahorro_estimado == 0 for p in ps)
    assert len(ps) <= MAXIMO_PETICIONES


# ─────────────────────────── el ciclo de la respuesta ───────────────────────────


def test_respuesta_afirmativa_pide_el_certificado_sin_volver_a_preguntar():
    """Un `sí` convierte la pregunta en una petición de documento, no la apaga."""
    respuestas = [Respuesta(pregunta="PREPAGADA", tiene=True, detalle={}, quien="c", cuando=AHORA)]
    ps = derivar_peticiones([], respuestas, CASO)
    prepagada = next(p for p in ps if p.tipo_documento == "CERT_PREPAGADA")
    assert prepagada.pregunta_previa is None


def test_el_beneficio_que_ya_esta_en_el_caso_no_se_vuelve_a_pedir():
    """El certificado llegó y el beneficio ya está capturado: la petición desaparece."""
    caso = CASO.model_copy(
        update={
            "beneficios": Beneficios(
                medicina_prepagada=MontoDeclarado(valor=4_000_000, fuente=Fuente.manual("captura"))
            )
        }
    )
    ps = derivar_peticiones([], [], caso)
    assert not any(p.tipo_documento == "CERT_PREPAGADA" for p in ps)


def test_cerrar_una_peticion_de_documento_la_apaga():
    """`POST /cerrar-peticion` guarda la misma `Respuesta` con la clave de la petición."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    [antes] = [p for p in derivar_peticiones(partidas, [], CASO) if p.tercero is not None]
    respuestas = [
        Respuesta(pregunta=antes.id, tiene=False, detalle={}, quien="contador", cuando=AHORA)
    ]
    ps = derivar_peticiones(partidas, respuestas, CASO)
    assert not any(p.id == antes.id for p in ps)


# ─────────────────────────── qué NO se pide ───────────────────────────


def test_la_partida_ajena_no_pide_certificado():
    """Es plata de otra persona: pedirle su certificado al cliente no tiene sentido."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    assert [p for p in derivar_peticiones(partidas, [], CASO) if p.tercero is not None] == []


def test_el_concepto_fuera_del_motor_no_pide_certificado():
    """El motor no liquida honorarios: el certificado no lo haría entrar al 210, y la
    salida de esa partida es LLEVAR_A_MANO en la cola de pendientes."""
    ps = derivar_peticiones([partida_honorarios()], [], CASO)
    assert [p for p in ps if p.tercero is not None] == []


def test_la_retencion_reportada_por_la_dian_no_pide_certificado():
    """La DIAN ya la reportó: no hay documento que agregue nada."""
    [retencion] = abrir(_exogena(fila_retencion("890903938", 560_000)))
    assert retencion.concepto is Concepto.RETENCION
    ps = derivar_peticiones([retencion], [], CASO)
    assert [p for p in ps if p.tercero is not None] == []


def test_una_provisional_del_sistema_no_apaga_la_peticion():
    """La partida sigue SOLO_DIAN: el certificado falta igual, y con él la retención y
    los aportes. Si la provisional la apagara, nadie volvería a pedir ese 220."""
    partidas = autorresolver(abrir(_exogena(_fila("900111222", "5001", 87_400_000))))
    assert partidas[0].resolucion is not None
    assert any(p.tercero is not None for p in derivar_peticiones(partidas, [], CASO))


def test_la_partida_conciliada_ya_no_pide_su_certificado():
    """El 220 llegó y la partida dejó de ser SOLO_DIAN: la petición se cumplió."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    conciliada = resolver(
        partidas[0].model_copy(update={"estado": EstadoPartida.DISCREPANCIA}),
        Decision.USAR_DIAN,
        motivo=Motivo.ERROR_DEL_CERTIFICADO,
        quien="contador",
    )
    ps = derivar_peticiones([conciliada], [], CASO)
    assert [p for p in ps if p.tercero is not None] == []


# ─────────────────────────── el ahorro y la prioridad ───────────────────────────


def test_el_220_del_asalariado_estima_el_ahorro_de_los_aportes():
    """La exógena no trae los aportes obligatorios y el 220 sí: eso es INCRNGO real, y
    es la única estimación defendible que se puede hacer sin ver el documento."""
    caso = _caso_con_el_laboral()
    partidas = autorresolver(abrir(_exogena(_fila("900111222", "5001", 87_400_000))))
    [peticion] = [p for p in derivar_peticiones(partidas, [], caso) if p.tercero is not None]
    assert peticion.tipo_documento == "CERT_INGRESOS_220"
    assert peticion.ahorro_estimado >= UMBRAL_AHORRO


def test_la_prioridad_es_el_puesto_en_la_lista():
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], _caso_con_el_laboral())
    assert [p.prioridad for p in ps] == list(range(1, len(ps) + 1))


def test_el_220_del_asalariado_va_antes_que_lo_no_estimable():
    """Lo que se puede medir manda sobre lo que no: el contador empieza por donde hay
    plata contable. (Arriba del 220 sí pueden ir los beneficios estimados en su tope
    legal, que es plata potencialmente mayor; lo que nunca puede pasarle por delante es
    una petición sin cifra.)"""
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], _caso_con_el_laboral())
    puesto_220 = next(p.prioridad for p in ps if p.tipo_documento == "CERT_INGRESOS_220")
    sin_estimar = [p.prioridad for p in ps if p.ahorro_estimado == 0]
    assert all(puesto_220 < otro for otro in sin_estimar)


def test_el_ahorro_del_beneficio_se_marca_como_techo_y_el_del_220_no():
    """Los dos números viven en la misma lista y significan cosas distintas: el del 220
    se mide con tarifas de ley sobre un pago ya reportado, el del beneficio es "hasta".
    Sin la marca, la interfaz le prometería al cliente una cifra que nadie sostiene."""
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], _caso_con_el_laboral())
    veinte = next(p for p in ps if p.tipo_documento == "CERT_INGRESOS_220")
    prepagada = next(p for p in ps if p.tipo_documento == "CERT_PREPAGADA")
    assert veinte.ahorro_estimado > 0 and veinte.ahorro_es_techo is False
    assert prepagada.ahorro_estimado > 0 and prepagada.ahorro_es_techo is True


def test_con_un_bloqueante_vivo_el_ahorro_no_se_promete_pero_la_lista_sale():
    """F9: `ahorro_marginal` optimizaba sin mirar los avisos del cruce, así que con un
    ingreso por fuera de la liquidación prometía un costo calculado sobre una base
    incompleta. Ahora el optimizador se niega y el ahorro sale como no estimable — pero la
    lista NO se cae, porque es justo lo que el contador necesita para salir del bloqueo."""
    llevada = resolver(
        partida_honorarios(),
        Decision.LLEVAR_A_MANO,
        motivo=Motivo.FUERA_DEL_MOTOR,
        quien="contador",
    )
    ps = derivar_peticiones([*PARTIDAS_VARIAS, llevada], [], _caso_con_el_laboral())
    assert ps, "la lista sigue saliendo"
    assert all(p.ahorro_estimado == 0 for p in ps)


def test_lo_no_estimable_no_se_marca_como_techo():
    """0 es "no se puede estimar", no "el techo es cero": marcarlo como techo diría que
    el beneficio no vale nada, que es exactamente lo contrario de lo que pasa."""
    ps = derivar_peticiones([], [], CASO)
    afc = next(p for p in ps if p.tipo_documento == "CERT_AFC_FVP")
    assert afc.ahorro_estimado == 0
    assert afc.ahorro_es_techo is False


def test_nunca_pasa_de_diez_peticiones():
    filas = [_fila(f"90000000{i}", "5001", 50_000_000, nombre=f"EMPLEADOR {i}") for i in range(9)]
    ps = derivar_peticiones(abrir(_exogena(*filas)), [], CASO)
    assert len(ps) == MAXIMO_PETICIONES


# ─────────────────────────── el copy y la partición ───────────────────────────


def test_toda_peticion_trae_copy_para_mandarle_al_cliente():
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], CASO)
    assert ps
    for p in ps:
        assert p.copy_sugerido.strip()
        assert p.razon.strip()
        assert isinstance(p, Peticion)


def test_un_concepto_nuevo_sin_decision_revienta_en_vez_de_callarse(monkeypatch):
    """La lección de los caminos paralelos: un `Concepto` que nadie clasifique ni como
    'tiene certificado' ni como 'no se pide' dejaría de pedir un documento EN SILENCIO."""
    from declaras.services.conciliacion import peticiones

    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    monkeypatch.setattr(peticiones, "_SIN_CERTIFICADO", frozenset())
    monkeypatch.setattr(peticiones, "_CERTIFICADO_POR_CONCEPTO", {})
    with pytest.raises(NotImplementedError, match="SALARIOS"):
        derivar_peticiones(partidas, [], CASO)


def test_los_conceptos_fuera_del_motor_estan_declarados_sin_certificado():
    """Si mañana el motor cubre independientes, sacarlos de un frozenset tiene que
    obligar a decidir su certificado en el mismo commit."""
    from declaras.services.conciliacion import peticiones

    assert CONCEPTOS_FUERA_DEL_MOTOR <= peticiones._SIN_CERTIFICADO


# ─────────────── el ahorro dice pesos de impuesto, y dice cuando no baja nada ───────────────
#
# "$ 0" no es una cifra: son tres situaciones que llevan a decisiones opuestas. Que un beneficio
# no baje nada significa que no vale la pena molestar al cliente; que no se pueda calcular
# significa que hay que desbloquear el caso primero. Sin decir cuál es, las dos se leen igual.


def test_un_beneficio_que_no_baja_el_impuesto_lo_dice_y_dice_por_que():
    """Un caso sin ingresos no paga impuesto, así que ninguna deducción lo baja. La cifra es
    cero de verdad y la razón es lo único accionable que hay."""
    ps = derivar_peticiones([], [], CASO, p=P)
    prepagada = next(p for p in ps if p.tipo_documento == "CERT_PREPAGADA")

    assert prepagada.ahorro_estimado == 0
    assert prepagada.ahorro_por_que is not None
    assert "no baja nada" in prepagada.ahorro_por_que
    assert "no queda impuesto que bajar" in prepagada.ahorro_por_que


def test_un_ahorro_medido_no_necesita_explicacion():
    """Cuando la cifra es una medición, la cifra habla sola: la explicación existe para cuando
    no la hay, y ponerla siempre la volvería ruido."""
    from tests.golden.casos import g2

    ps = derivar_peticiones([], [], g2(), p=P)
    con_ahorro = [p for p in ps if p.ahorro_estimado > 0]
    assert con_ahorro, "un asalariado con impuesto sí tiene beneficios que le bajan plata"
    assert all(p.ahorro_por_que is None for p in con_ahorro)


def test_el_ahorro_son_pesos_de_impuesto_no_de_base_gravable():
    """La distinción que pidió el producto, y la razón por la que decir "72 UVT" está mal.

    Un dependiente no vale 72 UVT: DESBLOQUEA DOS BENEFICIOS —los 72 UVT del artículo 336, que
    van por fuera del límite del 40%, y la deducción del artículo 387— así que la base baja
    $9.585.528 y no $3.585.528. Sobre eso, el impuesto baja $2.683.948, que es la tarifa marginal
    de ESTE contribuyente.

    O sea que la cifra que la gente repite subestima el beneficio a menos de la mitad, y la que
    se le puede prometer a alguien es la del impuesto, que depende de su caso.
    """
    from tests.golden.casos import g2

    caso = g2()
    dependientes = next(p for p in derivar_peticiones([], [], caso, p=P) if p.id == "DEPENDIENTES")

    tope_de_un_dependiente_en_la_base = 72 * P.uvt
    assert dependientes.ahorro_estimado > 0
    # La invariante que de verdad importa: una deducción nunca puede ahorrar más impuesto que su
    # propio monto. Si esto se rompe, el motor está regalando plata.
    assert dependientes.ahorro_estimado < tope_de_un_dependiente_en_la_base * 3

    # Y es impuesto, no base: comparado contra la reducción real de la base, es una fracción.
    from declaras.optimizador import optimizar
    from declaras.services.conciliacion.peticiones import _BENEFICIOS

    beneficio = next(b for b in _BENEFICIOS if b.pregunta == "DEPENDIENTES")
    baja_de_base = optimizar(caso, P).liquidacion.valor("RLG_GENERAL") - optimizar(
        beneficio.hipotesis(caso, P), P
    ).liquidacion.valor("RLG_GENERAL")
    assert dependientes.ahorro_estimado < baja_de_base, (
        "el ahorro de impuesto tiene que ser menor que la reducción de la base gravable"
    )
