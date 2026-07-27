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
    refrescar,
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


# ─────────────────────────── refrescar: datos nuevos vs resoluciones ───────────────────────────


def _cert_220_bis(sha: str, salarios: int = 85_000_000):
    """Otro escaneo/exportación del 220: mismo empleador, otro hash de bytes."""
    from declaras.documents.models import DocumentReading, ExtractedField

    campos = {"empleador_nit": "900111222", "empleador_nombre": "ACME SAS",
              "salarios": salarios, "retencion": 0}
    return DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256=sha * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )


def test_refrescar_reemplaza_siempre_la_provisional_del_sistema():
    """La provisional era un placeholder: cuando llega el documento y la partida pasa a
    COINCIDE, el automatismo nuevo la cierra con el documento — la provisional USAR_DIAN
    no se queda pegada a una partida que ya tiene las dos versiones."""
    exogena = _exogena(_fila("900111222", "5001", 85_000_000))
    guardadas = autorresolver(abrir(exogena))
    assert guardadas[0].resolucion.decision is Decision.USAR_DIAN  # la provisional
    nuevas = incorporar(abrir(exogena), _cert_220("900111222", 85_000_000))
    [p] = refrescar(nuevas, guardadas)
    assert p.resolucion.decision is Decision.USAR_DOCUMENTO
    assert p.resolucion.motivo is Motivo.COINCIDEN


def test_refrescar_preserva_la_decision_del_contador_si_la_huella_coincide():
    guardadas = [resolver(partida_discrepancia(), Decision.USAR_DOCUMENTO,
                          motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")]
    nuevas = incorporar(abrir(_exogena(_fila("900111222", "5001", 87_400_000))),
                        _cert_220("900111222", 85_000_000), tolerancia_pesos=0)
    [p] = refrescar(nuevas, guardadas)
    assert p.resolucion == guardadas[0].resolucion  # quien, cuando y nota intactos


def test_refrescar_preserva_aunque_la_dian_republique_con_la_fila_corrida():
    """Una republicación que solo mueve la fila (celda A20→A21) no cambia los valores:
    invalidar ahí sería trabajo inventado y la nota diría mentiras."""
    guardadas = [resolver(partida_discrepancia(), Decision.USAR_DIAN,
                          motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="contador@x.co")]
    republicada = _exogena(_fila("890903938", "5010", 1_000_000),  # fila insertada arriba
                           _fila("900111222", "5001", 87_400_000))
    nuevas = incorporar(abrir(republicada), _cert_220("900111222", 85_000_000),
                        tolerancia_pesos=0)
    objetivo = next(p for p in refrescar(nuevas, guardadas)
                    if p.id == "900111222:SALARIOS")
    assert objetivo.version_dian.celda == "A21"  # la fila sí se corrió
    assert objetivo.resolucion == guardadas[0].resolucion


def test_refrescar_invalida_si_los_valores_cambiaron():
    from declaras.services.conciliacion import NOTA_VALORES_CAMBIARON

    guardadas = [resolver(partida_discrepancia(), Decision.USAR_DOCUMENTO,
                          motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")]
    republicada = _exogena(_fila("900111222", "5001", 90_000_000))  # la DIAN corrigió
    nuevas = incorporar(abrir(republicada), _cert_220("900111222", 85_000_000),
                        tolerancia_pesos=0)
    [p] = refrescar(nuevas, guardadas)
    assert p.resolucion is None  # pendiente de nuevo: DISCREPANCIA no se autorresuelve
    assert NOTA_VALORES_CAMBIARON in (p.nota or "")


def test_refrescar_suma_la_nota_sin_pisar_la_del_cruce():
    """La nota fresca del cruce (p. ej. la marca de ajena) y la de valores cambiados
    conviven: reescribir en vez de sumar borraría lo que el cruce acaba de decir."""
    from declaras.services.conciliacion import NOTA_VALORES_CAMBIARON

    ajena = partida_ajena()
    guardadas = [resolver(ajena, Decision.MARCAR_AJENO,
                          motivo=Motivo.NO_ES_MIO, quien="contador@x.co")]
    republicada = _exogena(_fila("901999888", "5001", 12_000_000, reportado_a="99999"))
    [p] = refrescar(abrir(republicada), guardadas)
    assert p.resolucion is None
    assert NOTA_VALORES_CAMBIARON in (p.nota or "")
    assert "otra identificación" in (p.nota or "")


def test_refrescar_no_arrastra_la_resolucion_a_un_id_que_cambio():
    """El id inestable documentado en `_Grupo.id`: si el tercero sin NIT escribe su nombre
    distinto en la republicación, la resolución vieja queda huérfana y la partida nueva
    nace pendiente — nunca resuelta por arrastre."""
    fila_vieja = _fila("", "5001", 10_000_000, nombre="ACME S.A.S.")
    [vieja] = abrir(_exogena(fila_vieja))
    guardadas = [resolver(vieja, Decision.MARCAR_AJENO,
                          motivo=Motivo.NO_ES_MIO, quien="contador@x.co")]
    fila_nueva = _fila("", "5001", 10_000_000, nombre="ACME SAS")
    [p] = refrescar(abrir(_exogena(fila_nueva)), guardadas)
    assert p.id != vieja.id
    assert p.resolucion is None or p.resolucion.origen is Origen.SISTEMA
    assert p.resolucion is None or p.resolucion.decision is not Decision.MARCAR_AJENO


def test_refrescar_sin_nit_una_version_nueva_invalida_aunque_las_cifras_coincidan():
    """Sin NIT, un documento nuevo con cifras iguales sí es información nueva (¿mismo
    certificado repetido o dos terceros?, ruling de F1): la decisión vuelve a la persona.
    La huella cubre la membresía de `versiones_documento` solo en este camino."""
    from declaras.services.conciliacion import NOTA_VALORES_CAMBIARON
    from tests.unit.conciliacion.test_cruce import _cert_220_completo

    doc_c = _cert_220_completo("c", nit="", aportes_salud=0, aportes_pension=0)
    doc_d = _cert_220_completo("d", nit="", aportes_salud=0, aportes_pension=0)
    [suelta] = incorporar([], doc_c)
    guardadas = [resolver(suelta, Decision.USAR_DOCUMENTO,
                          motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")]
    nuevas = incorporar(incorporar([], doc_c), doc_d)
    [p] = refrescar(nuevas, guardadas)
    assert p.resolucion is None
    assert NOTA_VALORES_CAMBIARON in (p.nota or "")


def test_refrescar_con_nit_bytes_nuevos_con_cifras_iguales_no_invalidan():
    """Con NIT, mismas cifras = mismo certificado (F4): el PDF re-exportado no le
    inventa trabajo al contador ni tumba su decisión."""
    exogena = _exogena(_fila("900111222", "5001", 87_400_000))
    base = incorporar(abrir(exogena), _cert_220_bis("b"), tolerancia_pesos=0)
    guardadas = [resolver(base[0], Decision.USAR_DOCUMENTO,
                          motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")]
    nuevas = incorporar(base, _cert_220_bis("e"), tolerancia_pesos=0)  # re-exportado
    [p] = refrescar(nuevas, guardadas)
    assert set(p.versiones_documento) == {"b" * 12, "e" * 12}
    assert p.resolucion == guardadas[0].resolucion


def test_refrescar_autorresuelve_lo_nuevo():
    """Una partida que aparece por primera vez entra por los mismos automatismos:
    el preliminar existe en una sola llamada."""
    nuevas = abrir(_exogena(_fila("890903938", "5010", 8_000_000)))
    [p] = refrescar(nuevas, [])
    assert p.resolucion is not None
    assert p.resolucion.origen is Origen.SISTEMA


def test_refrescar_no_muta_sus_entradas():
    exogena = _exogena(_fila("900111222", "5001", 85_000_000))
    guardadas = autorresolver(abrir(exogena))
    nuevas = incorporar(abrir(exogena), _cert_220("900111222", 85_000_000))
    refrescar(nuevas, guardadas)
    assert nuevas[0].resolucion is None
    assert guardadas[0].resolucion.decision is Decision.USAR_DIAN


def test_refrescar_nunca_arrastra_una_provisional_del_sistema():
    """La provisional no es una decisión: NUNCA viaja de la lista guardada a la nueva,
    ni siquiera con la huella intacta — se descarta y el automatismo del final decide
    de cero. Si viajara, una partida que dejó de ser auto-resoluble conservando id y
    cifras (acá: se volvió ajena, el camino paralelo del guard) quedaría resuelta con
    plata de otra persona sin que nadie la mire. Mutación M4 del reporte: tratar
    SISTEMA como CONTADOR pasa el resto de la suite porque autorresolver converge en
    todos los demás caminos; este es el que diverge."""
    exogena = _exogena(_fila("901999888", "5001", 9_000_000))
    guardadas = autorresolver(abrir(exogena))
    assert guardadas[0].resolucion is not None
    nueva_ajena = abrir(exogena)[0].model_copy(update={"reportado_a": "99999"})
    [p] = refrescar([nueva_ajena], guardadas)
    assert p.resolucion is None
