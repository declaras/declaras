import pytest

from declaras.services.conciliacion import (
    Decision,
    EstadoPartida,
    Motivo,
    Origen,
    Partida,
    abrir,
    autorresolver,
    incorporar,
    pendientes,
    resolver,
)
from tests.unit.conciliacion.fabricas import (
    partida_ajena,
    partida_coincide,
    partida_concepto_desconocido,
    partida_discrepancia,
    partida_solo_dian,
    partida_solo_documento,
)
from tests.unit.conciliacion.test_cruce import _cert_220, _exogena, _fila

# ─────────────────────────── contrato del brief, literal ───────────────────────────


def test_autorresuelve_solo_las_coincidentes():
    resueltas = autorresolver([partida_coincide(), partida_discrepancia()])
    assert resueltas[0].resolucion.motivo is Motivo.COINCIDEN
    assert resueltas[0].resolucion.quien == "sistema"
    assert resueltas[1].resolucion is None


def test_resolver_discrepancia_a_favor_del_documento():
    p = resolver(partida_discrepancia(), Decision.USAR_DOCUMENTO,
                 motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")
    assert p.resolucion.valor == 85_000_000
    assert p.resolucion.decision is Decision.USAR_DOCUMENTO


def test_no_se_puede_usar_documento_que_no_existe():
    with pytest.raises(ValueError, match="SOLO_DIAN"):
        resolver(partida_solo_dian(), Decision.USAR_DOCUMENTO,
                 motivo=Motivo.ERROR_DEL_TERCERO, quien="x")


def test_usar_otro_exige_valor():
    with pytest.raises(ValueError, match="valor"):
        resolver(partida_discrepancia(), Decision.USAR_OTRO,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="x")


def test_pendientes_ordena_por_plata_en_juego():
    ps = pendientes([partida_discrepancia(diferencia=100),
                     partida_discrepancia(diferencia=9_000_000)])
    assert ps[0].diferencia_monto == 9_000_000


# ─────────────────────────── resolver: la tabla y los bordes ───────────────────────────

# La tabla de decisiones del brief, más una desviación autorizada por la herencia de T4
# (riesgo 2 de la ronda 2): CERRAR_SIN_SOPORTE también aplica a SOLO_DOCUMENTO, porque la
# partida suelta sin NIT que duplica una ya conciliada tiene que poder cerrarse SIN aportar
# hecho — con la tabla literal, sus únicas salidas metían la misma plata dos veces al caso.
_CASOS_TABLA = [
    (partida_coincide, {Decision.USAR_DOCUMENTO, Decision.USAR_DIAN}),
    (partida_discrepancia, {Decision.USAR_DOCUMENTO, Decision.USAR_DIAN, Decision.USAR_OTRO}),
    (partida_solo_dian, {Decision.USAR_DIAN, Decision.MARCAR_AJENO, Decision.USAR_OTRO}),
    (partida_solo_documento,
     {Decision.USAR_DOCUMENTO, Decision.USAR_OTRO, Decision.CERRAR_SIN_SOPORTE}),
    (partida_concepto_desconocido, {Decision.MARCAR_AJENO, Decision.CERRAR_SIN_SOPORTE}),
]


@pytest.mark.parametrize(
    ("fabrica", "permitidas"), _CASOS_TABLA, ids=lambda x: getattr(x, "__name__", None)
)
def test_la_tabla_de_decisiones_se_aplica_completa(fabrica, permitidas):
    for decision in Decision:
        argumentos = {"valor": 1_000_000} if decision is Decision.USAR_OTRO else {}
        if decision in permitidas:
            p = resolver(fabrica(), decision, motivo=Motivo.DECISION_DEL_CONTADOR,
                         quien="contador@x.co", **argumentos)
            assert p.resolucion is not None
        else:
            with pytest.raises(ValueError, match=str(fabrica().estado)):
                resolver(fabrica(), decision, motivo=Motivo.DECISION_DEL_CONTADOR,
                         quien="contador@x.co", **argumentos)


def test_resolver_no_muta_la_partida_original():
    original = partida_discrepancia()
    resuelta = resolver(original, Decision.USAR_DIAN,
                        motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="contador@x.co")
    assert original.resolucion is None
    assert resuelta.resolucion is not None
    assert resuelta.resolucion.origen is Origen.CONTADOR


def test_resolver_conserva_el_estado_del_cruce():
    """La resolución se ADJUNTA; el estado sigue contando qué desenlace tuvo el cruce."""
    p = resolver(partida_discrepancia(), Decision.USAR_DOCUMENTO,
                 motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")
    assert p.estado is EstadoPartida.DISCREPANCIA


def test_usar_dian_toma_el_valor_de_la_version_dian():
    p = resolver(partida_discrepancia(), Decision.USAR_DIAN,
                 motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="contador@x.co")
    assert p.resolucion.valor == 87_400_000


def test_usar_otro_guarda_el_valor_dado():
    p = resolver(partida_discrepancia(), Decision.USAR_OTRO,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co",
                 valor=86_000_000, nota="promedio de los extractos")
    assert p.resolucion.valor == 86_000_000
    assert p.resolucion.nota == "promedio de los extractos"


def test_usar_otro_no_acepta_valor_negativo():
    with pytest.raises(ValueError, match="negativo"):
        resolver(partida_discrepancia(), Decision.USAR_OTRO,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="x", valor=-1)


def test_el_valor_explicito_solo_acompania_a_usar_otro():
    """Un valor que se ignora en silencio es una decisión del contador que se pierde."""
    with pytest.raises(ValueError, match="USAR_OTRO"):
        resolver(partida_discrepancia(), Decision.USAR_DIAN,
                 motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="x", valor=86_000_000)


def test_marcar_ajeno_y_cerrar_sin_soporte_no_llevan_valor():
    p = resolver(partida_solo_dian(), Decision.MARCAR_AJENO,
                 motivo=Motivo.NO_ES_MIO, quien="contador@x.co")
    assert p.resolucion.valor == 0
    q = resolver(partida_concepto_desconocido(), Decision.CERRAR_SIN_SOPORTE,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")
    assert q.resolucion.valor == 0


def test_resolver_de_nuevo_reemplaza_la_resolucion():
    """El contador puede corregirse: la resolución nueva pisa la anterior."""
    primera = resolver(partida_discrepancia(), Decision.USAR_DIAN,
                       motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="a@x.co")
    segunda = resolver(primera, Decision.USAR_DOCUMENTO,
                       motivo=Motivo.ERROR_DEL_TERCERO, quien="b@x.co")
    assert segunda.resolucion.decision is Decision.USAR_DOCUMENTO
    assert segunda.resolucion.quien == "b@x.co"


def test_la_huella_es_un_digest_completo():
    """Misma convención que `content_sha256` (T3): 64 hex, el corto se deriva después."""
    p = resolver(partida_discrepancia(), Decision.USAR_DIAN,
                 motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="x")
    assert len(p.resolucion.huella) == 64
    assert set(p.resolucion.huella) <= set("0123456789abcdef")


def test_las_marcas_estructurales_sobreviven_la_resolucion():
    """`reportado_a`, `versiones_documento`, `version_que_rige` y `documentos_por_cruzar`
    tienen que salir intactos de `resolver` y del viaje dump→validate con resolución."""
    base = partida_solo_documento()
    p = resolver(base, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR,
                 quien="contador@x.co")
    assert p.versiones_documento == base.versiones_documento
    assert p.version_que_rige == base.version_que_rige
    assert p.reportado_a == base.reportado_a
    assert p.documentos_por_cruzar == base.documentos_por_cruzar
    revivida = Partida.model_validate(p.model_dump())
    assert revivida == p
    assert revivida.resolucion == p.resolucion


# ─────────────────────────── autorresolver: los dos automatismos ───────────────────────────


def test_autorresolver_cierra_coincide_con_el_documento():
    [p] = autorresolver([partida_coincide()])
    assert p.resolucion.decision is Decision.USAR_DOCUMENTO
    assert p.resolucion.origen is Origen.SISTEMA
    assert p.resolucion.valor == 85_000_000


def test_autorresolver_pone_provisional_a_solo_dian():
    """El 210 preliminar existe sin esperar documentos: provisional USAR_DIAN."""
    [p] = autorresolver([partida_solo_dian()])
    assert p.resolucion.decision is Decision.USAR_DIAN
    assert p.resolucion.motivo is Motivo.FALTA_DOCUMENTO
    assert p.resolucion.origen is Origen.SISTEMA
    assert p.resolucion.valor == 9_000_000


def test_autorresolver_nunca_toca_una_ajena():
    """El guard de la herencia de T4: sin él, la provisional USAR_DIAN liquidaría plata
    de otra persona en el 210 preliminar. La marca es `reportado_a`, estructural."""
    [p] = autorresolver([partida_ajena()])
    assert p.resolucion is None


def test_autorresolver_no_toca_ajenas_en_ningun_estado():
    """El camino paralelo: una ajena construida en otro estado (a mano, o por un cruce
    futuro) tampoco puede auto-cerrarse — el guard va antes de mirar el estado."""
    disfrazada = partida_coincide().model_copy(update={"reportado_a": "99999"})
    [p] = autorresolver([disfrazada])
    assert p.resolucion is None


def test_autorresolver_deja_en_persona_lo_que_no_es_automatico():
    resueltas = autorresolver([partida_solo_documento(), partida_concepto_desconocido()])
    assert all(p.resolucion is None for p in resueltas)


def test_autorresolver_respeta_las_resoluciones_existentes():
    """Una decisión del contador no se pisa por volver a correr el automatismo."""
    del_contador = resolver(partida_coincide(), Decision.USAR_DIAN,
                            motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")
    [p] = autorresolver([del_contador])
    assert p.resolucion == del_contador.resolucion


def test_autorresolver_no_muta_la_lista_de_entrada():
    partidas = [partida_coincide()]
    autorresolver(partidas)
    assert partidas[0].resolucion is None


# ─────────────────────────── pendientes: la cola del contador ───────────────────────────


def test_pendientes_excluye_las_resueltas():
    resueltas = autorresolver([partida_coincide(), partida_solo_dian(),
                               partida_discrepancia()])
    quedan = pendientes(resueltas)
    assert [p.estado for p in quedan] == [EstadoPartida.DISCREPANCIA]


def test_pendientes_no_hunde_la_discrepancia_de_retencion():
    """Una discrepancia SOLO en la retención tiene la plata en juego de su retención:
    ordenar solo por `diferencia_monto` la mandaba al final de la cola."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000,
                                    retencion=8_000_000)))
    [por_retencion] = incorporar(partidas, _cert_220("900111222", 85_000_000,
                                                     retencion=6_000_000))
    ps = pendientes([partida_discrepancia(diferencia=100), por_retencion])
    assert ps[0] is por_retencion


def test_pendientes_pesa_una_sola_version_por_su_monto():
    """En una partida de un solo lado TODO el monto está en juego, no la diferencia (0):
    una suelta de 85M no puede quedar debajo de una discrepancia de 100 pesos."""
    ps = pendientes([partida_discrepancia(diferencia=100), partida_solo_documento(),
                     partida_ajena()])
    assert ps[0].estado is EstadoPartida.SOLO_DOCUMENTO  # 85M en juego
    assert ps[1].reportado_a is not None  # la ajena pesa por sus 9M de la DIAN
    assert ps[2].diferencia_monto == 100
