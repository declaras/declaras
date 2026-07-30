from datetime import UTC, datetime

import pytest

from declaras.caso import (
    Beneficios,
    Contribuyente,
    Creditos,
    Fuente,
    MontoDeclarado,
    Patrimonio,
)
from declaras.services.conciliacion import (
    Concepto,
    Decision,
    EstadoPartida,
    Lado,
    Motivo,
    Origen,
    Partida,
    Resolucion,
    Valor,
    a_caso,
    abrir,
    autorresolver,
    avisos,
    incorporar,
    resolver,
)
from tests.unit.conciliacion.fabricas import (
    fila_retencion,
    partida_ajena,
    partida_coincide,
    partida_concepto_desconocido,
    partida_discrepancia,
    partida_dividendos,
    partida_honorarios,
    partida_pension,
    partida_retencion,
    partida_solo_dian,
)
from tests.unit.conciliacion.test_cruce import (
    _cert_220,
    _cert_220_completo,
    _exogena,
    _fila,
)

CONTRIB = Contribuyente(num_doc="1234567", nombre="Prueba")


def _a_caso(partidas):
    return a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)


# ─────────────────────────── contrato del brief ───────────────────────────


def test_no_se_puede_liquidar_con_partidas_pendientes():
    with pytest.raises(ValueError, match="1 partida"):
        a_caso([partida_discrepancia()], contribuyente=CONTRIB, anio_gravable=2025)


def test_partida_resuelta_produce_hecho_con_proveniencia():
    caso = a_caso(autorresolver([partida_coincide()]), contribuyente=CONTRIB, anio_gravable=2025)
    assert len(caso.laborales) == 1
    lab = caso.laborales[0]
    assert lab.salarios == 85_000_000
    assert lab.fuente.clase == "conciliacion"
    # ADAPTADO del brief (que decía "900111222:5001"): el id de la partida es nit:CONCEPTO,
    # no nit:código — ruling del coordinador en el commit 0b51669 (varios códigos oficiales
    # caen en un mismo concepto y el código crudo partiría una partida en dos ids).
    assert lab.fuente.ref == "900111222:SALARIOS"


def test_ajeno_no_entra_al_caso():
    p = resolver(partida_solo_dian(), Decision.MARCAR_AJENO, motivo=Motivo.NO_ES_MIO, quien="x")
    caso = a_caso([p], contribuyente=CONTRIB, anio_gravable=2025)
    assert caso.laborales == []
    assert caso.ingresos_brutos_totales == 0


# ─────────────────────────── el ensamble por tercero ───────────────────────────


def _partidas_laborales_completas():
    """Exógena con la fila 5001 y la fila R132 del MISMO empleador, más el 220 completo:
    el escenario de la herencia de T4 donde la retención vive en dos sitios."""
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]  # el XLSX real no trae columna de retención en la fila de ingreso
    exogena = _exogena(fila, fila_retencion("900111222", 8_000_000))
    partidas = incorporar(abrir(exogena), _cert_220_completo("a", retencion=8_000_000))
    partidas = autorresolver(partidas)
    # Los aportes del 220 nacen SOLO_DOCUMENTO: los decide una persona.
    return [
        p
        if p.resolucion is not None
        else resolver(
            p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )
        for p in partidas
    ]


def test_el_laboral_se_ensambla_con_los_aportes_del_mismo_tercero():
    caso = _a_caso(_partidas_laborales_completas())
    assert len(caso.laborales) == 1
    lab = caso.laborales[0]
    assert lab.salarios == 85_000_000
    assert lab.aportes_salud == 3_400_000
    assert lab.aportes_pension == 3_600_000
    assert lab.empleador_nit == "900111222"


def test_la_retencion_del_mismo_tercero_no_se_suma_de_las_dos_fuentes():
    """El riesgo 1 de la herencia de T4, medido: la retención del empleador vive en la
    partida RETENCION (lado DIAN) Y dentro del 220 (version_documento.retencion). Sumarlas
    declara 16M donde hay 8M → saldo a favor inflado. Rige UNA fuente: la partida
    RETENCION explícita, que es la fila que la propia DIAN asignó al renglón 132."""
    caso = _a_caso(_partidas_laborales_completas())
    assert caso.laborales[0].retencion == 8_000_000  # nunca 16_000_000


def test_sin_partida_de_retencion_rige_la_version_escogida():
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]
    partidas = incorporar(
        abrir(_exogena(fila)), _cert_220("900111222", 85_000_000, retencion=8_000_000)
    )
    caso = _a_caso(autorresolver(partidas))
    assert caso.laborales[0].retencion == 8_000_000  # la del 220, que es lo que se usó


def test_la_retencion_explicita_no_se_reparte_a_las_gemelas():
    """Titular y ajena reclamada del mismo NIT: la retención explícita va UNA vez, al
    ingreso del titular (primero en el orden), no a cada laboral del grupo."""
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]
    ajena = _fila("900111222", "5001", 9_000_000, reportado_a="99999")
    exogena = _exogena(fila, ajena, fila_retencion("900111222", 8_000_000))
    partidas = incorporar(abrir(exogena), _cert_220("900111222", 85_000_000))
    partidas = autorresolver(partidas)
    partidas = [
        p
        if p.resolucion is not None
        else resolver(
            p, Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )  # el contador reclama la ajena: sí es del titular
        for p in partidas
    ]
    caso = _a_caso(partidas)
    assert len(caso.laborales) == 2
    retenciones = sorted(lab.retencion for lab in caso.laborales)
    assert retenciones == [0, 8_000_000]  # una vez, no 8M en cada laboral
    del_titular = next(lab for lab in caso.laborales if lab.salarios == 85_000_000)
    assert del_titular.retencion == 8_000_000


def test_la_retencion_sin_ingreso_no_entra_y_avisa():
    """El caso real del fixture calibrado: un banco reporta solo la fila R132. Declararla
    sin ingreso fabrica saldo a favor sin sustento; perderla en silencio regala plata del
    cliente. No entra Y queda el aviso para que el contador la vea en el borrador."""
    partidas = autorresolver([partida_retencion()])
    caso = _a_caso(partidas)
    assert caso.ingresos_brutos_totales == 0
    presentes = avisos(partidas)
    assert any(f.codigo == "RETENCION_SIN_INGRESO" for f in presentes)


def test_la_pension_se_reparte_en_12_mesadas_iguales_y_avisa():
    partidas = autorresolver([partida_pension(total=66_000_000)])
    caso = _a_caso(partidas)
    [pension] = caso.pensiones
    assert pension.mesadas == [5_500_000] * 12
    assert pension.pagador == "COLPENSIONES"
    assert any(f.codigo == "PENSION_DISTRIBUIDA_UNIFORME" for f in avisos(partidas))


def test_el_reparto_de_la_pension_no_pierde_ni_inventa_pesos():
    """El total no divisible por 12 tiene que sumar EXACTO: redondear cada mesada
    perdería o inventaría pesos (el único punto de redondeo del sistema es dinero.pesos,
    y acá no hay nada que redondear: es división entera con resto)."""
    partidas = autorresolver([partida_pension(total=50_000_007)])
    [pension] = _a_caso(partidas).pensiones
    assert sum(pension.mesadas) == 50_000_007
    assert max(pension.mesadas) - min(pension.mesadas) <= 1


def test_rendimientos_y_arriendos_mapean_con_su_retencion():
    exogena = _exogena(
        _fila("890903938", "5010", 8_000_000, retencion=560_000, nombre="BANCO Y"),
        _fila("901333555", "5005", 36_000_000, nombre="INMOBILIARIA Z"),
    )
    caso = _a_caso(autorresolver(abrir(exogena)))
    [rend] = caso.rendimientos
    assert (rend.entidad, rend.valor, rend.retencion) == ("BANCO Y", 8_000_000, 560_000)
    [arr] = caso.arriendos
    assert (arr.canon_total, arr.retencion) == (36_000_000, 0)
    assert arr.costos.total == 0  # los costos no salen del cruce: van por beneficios/captura


def test_los_dividendos_entran_como_gravados_y_avisan():
    """La partida trae UN número y el modelo exige el desglose gravados/no gravados. Se
    asume gravados —la dirección que nunca subdeclara, como el componente inflacionario
    en 0%— y el aviso deja la decisión a la vista del contador."""
    partidas = autorresolver([partida_dividendos(total=14_000_000)])
    [div] = _a_caso(partidas).dividendos
    assert div.gravados == 14_000_000
    assert div.no_gravados == 0
    assert any(f.codigo == "DIVIDENDOS_SIN_DESAGREGAR" for f in avisos(partidas))


def test_no_se_puede_meter_un_hecho_de_honorarios_sin_decir_a_que_cedula_va():
    """La guarda está en `resolver`, una capa antes del ensamble, y eso es el arreglo.

    Antes `USAR_DIAN` sobre un renglón de honorarios era una decisión válida y el ensamble
    reventaba después. Desde afuera se veía así: el contador aceptaba la cifra que la DIAN
    reportó, la interfaz decía "listo" y el caso quedaba sin armar, con el borrador en 409.
    Pasó en un caso real.

    Las salidas son CLASIFICAR (lo mete en su cédula) y LLEVAR_A_MANO (lo saca con aviso).
    """
    [p] = abrir(_exogena(_fila("901222333", "5002", 10_000_000)))
    with pytest.raises(ValueError, match="CLASIFICAR"):
        resolver(p, Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")


def test_el_backstop_del_ensamble_sigue_vivo_para_una_partida_construida_a_mano():
    """La guarda de `resolver` no vuelve inalcanzable la del ensamble.

    `Partida` no tiene validadores, así que una resolución puesta con `model_copy` se salta
    `resolver` entero. Silenciar eso haría desaparecer una cédula completa de la declaración.
    """
    [p] = abrir(_exogena(_fila("901222333", "5002", 10_000_000)))
    a_mano = p.model_copy(
        update={
            "resolucion": Resolucion(
                decision=Decision.USAR_DIAN,
                valor=10_000_000,
                motivo=Motivo.DECISION_DEL_CONTADOR,
                origen=Origen.CONTADOR,
                huella="sin verificar",
                quien="quien se salta resolver",
                cuando=datetime(2026, 7, 29, tzinfo=UTC),
            )
        }
    )
    with pytest.raises(NotImplementedError, match="HONORARIOS"):
        _a_caso([a_mano])


def test_cerrar_sin_soporte_no_aporta_hecho():
    p = resolver(
        partida_concepto_desconocido(),
        Decision.CERRAR_SIN_SOPORTE,
        motivo=Motivo.DECISION_DEL_CONTADOR,
        quien="contador@x.co",
    )
    caso = _a_caso([p])
    assert caso.ingresos_brutos_totales == 0


def test_las_provisionales_del_sistema_si_liquidan():
    """Son el 210 preliminar: a_caso no distingue origen, solo exige que no haya
    pendientes de persona."""
    partidas = autorresolver(abrir(_exogena(_fila("900111222", "5001", 87_400_000))))
    caso = _a_caso(partidas)
    assert caso.laborales[0].salarios == 87_400_000


def test_un_hecho_sin_concepto_revienta():
    """Defensa contra partidas construidas a mano: la tabla de decisiones ya impide
    resolver una CONCEPTO_DESCONOCIDO con hecho, pero `Partida` no valida coherencia."""
    base = partida_concepto_desconocido()
    resuelta = resolver(
        partida_solo_dian(), Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR, quien="x"
    )
    incoherente = base.model_copy(update={"resolucion": resuelta.resolucion})
    with pytest.raises(ValueError, match="concepto"):
        _a_caso([incoherente])


def test_el_conteo_de_pendientes_es_el_real():
    with pytest.raises(ValueError, match="3 partidas"):
        _a_caso([partida_discrepancia(), partida_ajena(), partida_concepto_desconocido()])


def test_la_ajena_sin_resolver_bloquea_el_caso():
    """Ni el automatismo la toca ni el caso se arma sin ella: una persona la marca.
    (El precio documentado del guard de la herencia: sin persona no hay preliminar
    cuando la exógena trae filas ajenas.)"""
    with pytest.raises(ValueError, match="1 partida"):
        _a_caso(autorresolver([partida_ajena()]))


def test_aportes_resueltos_sin_ingreso_laboral_revientan():
    """El horror que T4 documentó (IngresoLaboral con 0 de salario y los aportes
    completos) no se construye en silencio: es una contradicción entre resoluciones."""
    from declaras.documents.models import DocumentReading, ExtractedField

    campos = {
        "empleador_nit": "900111222",
        "empleador_nombre": "ACME SAS",
        "aportes_salud": 3_400_000,
    }
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220",
        parser="test",
        content_sha256="a" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    [p] = incorporar([], doc)
    resuelta = resolver(
        p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
    )
    with pytest.raises(ValueError, match="aportes"):
        _a_caso([resuelta])


def test_beneficios_patrimonio_y_creditos_entran_como_llegan():
    beneficios = Beneficios(
        medicina_prepagada=MontoDeclarado(valor=6_000_000, fuente=Fuente.manual("contador@x.co"))
    )
    creditos = Creditos(anticipo_pagado=1_000_000)
    caso = a_caso(
        [],
        contribuyente=CONTRIB,
        anio_gravable=2025,
        beneficios=beneficios,
        patrimonio=Patrimonio(),
        creditos=creditos,
    )
    assert caso.beneficios == beneficios
    assert caso.creditos.anticipo_pagado == 1_000_000
    assert caso.anio_gravable == 2025


def test_la_fuente_arrastra_la_procedencia_de_la_version_escogida():
    [p] = autorresolver([partida_coincide()])
    caso = _a_caso([p])
    fuente = caso.laborales[0].fuente
    assert fuente.celda == p.version_documento.celda
    assert fuente.confianza == 0.97
    assert "USAR_DOCUMENTO" in (fuente.detalle or "")
    assert "sistema" in (fuente.detalle or "")


def test_usar_otro_entra_con_el_valor_del_contador_y_sin_procedencia_prestada():
    p = resolver(
        partida_discrepancia(),
        Decision.USAR_OTRO,
        motivo=Motivo.DECISION_DEL_CONTADOR,
        quien="contador@x.co",
        valor=86_000_000,
    )
    caso = _a_caso([p])
    lab = caso.laborales[0]
    assert lab.salarios == 86_000_000
    assert lab.fuente.celda is None  # el número no salió de ninguna celda
    assert lab.fuente.confianza is None
    # Las dos versiones afirman retención 0: la recuperación de C2 (caer a la afirmación
    # que exista cuando la escogida no afirma) devuelve ese 0 afirmado, no un default.
    assert lab.retencion == 0


def test_las_sueltas_sin_nit_del_mismo_documento_se_ensamblan_juntas():
    """El 220 sin NIT abre tres partidas sueltas (salarios + los dos aportes): son el
    mismo certificado y arman UN laboral, no un laboral sin aportes más aportes perdidos."""
    partidas = incorporar([], _cert_220_completo("c", nit=""))
    partidas = [
        resolver(
            p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )
        for p in partidas
    ]
    caso = _a_caso(partidas)
    [lab] = caso.laborales
    assert (lab.salarios, lab.aportes_salud, lab.aportes_pension) == (
        85_000_000,
        3_400_000,
        3_600_000,
    )
    assert lab.empleador_nit == ""


def test_avisos_sin_nada_que_avisar_esta_vacio():
    assert avisos(autorresolver([partida_coincide()])) == []


# ─────────── ronda de fixes 1: la salida para conceptos fuera del motor ───────────


def test_llevada_a_mano_no_bloquea_el_caso_y_deja_aviso_bloqueante():
    """El ruling de la ronda 1: la partida fuera del alcance del motor sale de la
    liquidación por decisión del contador — pero excluir un ingreso es subdeclarar,
    así que la exclusión JAMÁS es silenciosa (tercero, concepto y cifra en el aviso)
    ni informativa (bloqueante: nadie presenta ese 210 creyendo que está completo)."""
    p = resolver(
        partida_honorarios(monto=10_000_000),
        Decision.LLEVAR_A_MANO,
        motivo=Motivo.FUERA_DEL_MOTOR,
        quien="contador@x.co",
    )
    caso = _a_caso([p])
    assert caso.ingresos_brutos_totales == 0  # no aporta hecho
    [aviso] = avisos([p])
    assert aviso.codigo == "INGRESO_LLEVADO_A_MANO"
    assert aviso.severidad == "bloqueante"
    assert "ZETA SAS" in aviso.mensaje
    assert "HONORARIOS" in aviso.mensaje
    assert "$10.000.000" in aviso.mensaje


def test_el_aviso_de_llevada_a_mano_dice_las_dos_cifras_si_difieren():
    """Con las dos versiones en disputa el contador necesita ver ambas: va a sumar a
    mano y decidir cuál usa es exactamente su trabajo pendiente."""
    discrepante = Partida(
        id="901222333:HONORARIOS",
        nit_tercero="901222333",
        nombre_tercero="ZETA SAS",
        concepto=Concepto.HONORARIOS,
        version_dian=Valor(monto=10_000_000, retencion=None, lado=Lado.DIAN),
        version_documento=Valor(monto=9_000_000, retencion=None, lado=Lado.DOCUMENTO),
        estado=EstadoPartida.DISCREPANCIA,
    )
    p = resolver(
        discrepante, Decision.LLEVAR_A_MANO, motivo=Motivo.FUERA_DEL_MOTOR, quien="contador@x.co"
    )
    [aviso] = avisos([p])
    assert "$10.000.000" in aviso.mensaje
    assert "$9.000.000" in aviso.mensaje


# ─────────── ronda de fixes 2: C2 — la retención certificada no se tira ───────────


def _partida_usar_dian_con_retencion_solo_en_el_220():
    """El escenario C2: exógena real (sin columna de retención → el lado DIAN no la
    afirma), 220 con retención certificada, empleador SIN fila R132."""
    fila = _fila("900111222", "5001", 87_400_000)
    del fila["retencion"]
    partidas = incorporar(
        abrir(_exogena(fila)),
        _cert_220("900111222", 85_000_000, retencion=8_000_000),
        tolerancia_pesos=0,
    )
    return partidas[0]


def test_usar_dian_no_tira_la_retencion_que_solo_el_220_certifica():
    """C2: la decisión del contador es sobre el MONTO; la retención ni siquiera está en
    disputa (un lado no la reporta → diferencia 0 por diseño de T4). Elegir USAR_DIAN
    con ERROR_DEL_CERTIFICADO — el uso natural del motivo — declaraba retención 0: None
    NO es 0 (la invariante de T4 con nombre y apellido), y la única afirmación que
    existe es la del 220. Sigue rigiendo UNA fuente, no una suma."""
    p = resolver(
        _partida_usar_dian_con_retencion_solo_en_el_220(),
        Decision.USAR_DIAN,
        motivo=Motivo.ERROR_DEL_CERTIFICADO,
        quien="contador@x.co",
    )
    caso = _a_caso([p])
    assert caso.laborales[0].salarios == 87_400_000  # la decisión del contador, intacta
    assert caso.laborales[0].retencion == 8_000_000  # la única afirmación que existe


def test_usar_otro_tampoco_tira_la_retencion_afirmada():
    p = resolver(
        _partida_usar_dian_con_retencion_solo_en_el_220(),
        Decision.USAR_OTRO,
        motivo=Motivo.DECISION_DEL_CONTADOR,
        quien="contador@x.co",
        valor=86_000_000,
    )
    caso = _a_caso([p])
    assert caso.laborales[0].retencion == 8_000_000


def test_la_retencion_afirmada_en_cero_por_la_version_escogida_sigue_siendo_cero():
    """El otro lado de la invariante: un 0 AFIRMADO por la versión escogida es una
    afirmación, no una ausencia — no se va a buscar la de la otra versión."""
    fila = _fila("900111222", "5001", 87_400_000, retencion=0)  # la DIAN afirma 0
    partidas = incorporar(
        abrir(_exogena(fila)),
        _cert_220("900111222", 85_000_000, retencion=8_000_000),
        tolerancia_pesos=0,
    )
    p = resolver(
        partidas[0], Decision.USAR_DIAN, motivo=Motivo.ERROR_DEL_CERTIFICADO, quien="contador@x.co"
    )
    assert _a_caso([p]).laborales[0].retencion == 0


def test_un_concepto_que_nadie_ensambla_revienta_en_vez_de_desaparecer(monkeypatch):
    """I1 de la ronda 2: `conceptos.py` es tabla INCREMENTAL — un Concepto nuevo que
    nadie agregue ni a `_ORDEN_INGRESOS` ni a `CONCEPTOS_FUERA_DEL_MOTOR` se caía del
    caso EN SILENCIO (20M resueltos con hecho → ingresos_brutos_totales = 0, sin
    excepción y sin aviso). El repro del revisor: quitar ARRENDAMIENTOS de la tupla."""
    from declaras.services.conciliacion import mapeo

    monkeypatch.setattr(
        mapeo,
        "_ORDEN_INGRESOS",
        tuple(c for c in mapeo._ORDEN_INGRESOS if c is not Concepto.ARRENDAMIENTOS),
    )
    partidas = autorresolver(
        abrir(_exogena(_fila("901333555", "5005", 20_000_000, nombre="INMOBILIARIA Z")))
    )
    with pytest.raises(NotImplementedError, match="ARRENDAMIENTOS"):
        _a_caso(partidas)
    with pytest.raises(NotImplementedError, match="ARRENDAMIENTOS"):
        avisos(partidas)


def test_la_misma_partida_dos_veces_revienta_en_vez_de_duplicar_la_plata():
    """I3 de la ronda 2: el cruce ya advierte que dos partidas pueden nacer con el mismo
    id, y T6 va a persistir y reensamblar listas — la misma partida dos veces declaraba
    170M y 16M de retención sin aviso. El ensamble exige ids únicos."""
    [p] = autorresolver([partida_coincide()])
    with pytest.raises(ValueError, match="900111222:SALARIOS"):
        _a_caso([p, p])
    with pytest.raises(ValueError, match="900111222:SALARIOS"):
        avisos([p, p])


def test_desplazar_una_retencion_certificada_distinta_deja_aviso():
    """I4 de la ronda 2: la prioridad de la fuente explícita está bien (ratificada),
    pero desplazar EN SILENCIO una retención certificada distinta escondía plata — R132
    de 1M contra 8M certificados por el 220: se declaraba 1M y nadie veía los 7M."""
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]
    exogena = _exogena(fila, fila_retencion("900111222", 1_000_000))
    partidas = autorresolver(
        incorporar(abrir(exogena), _cert_220("900111222", 85_000_000, retencion=8_000_000))
    )
    caso = _a_caso(partidas)
    assert caso.laborales[0].retencion == 1_000_000  # la prioridad no cambia
    [aviso] = [f for f in avisos(partidas) if f.codigo == "RETENCION_DESPLAZADA"]
    assert "$1.000.000" in aviso.mensaje
    assert "$8.000.000" in aviso.mensaje


def test_borrar_la_retencion_con_un_cero_explicito_tambien_avisa():
    """El pariente del I4: resolver la partida RETENCION con USAR_OTRO valor=0 borra la
    certificada — un 0 explícito es indistinguible de 'no hay fuente explícita' y los
    8M desaparecían en silencio."""
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]
    exogena = _exogena(fila, fila_retencion("900111222", 8_000_000))
    partidas = autorresolver(
        incorporar(abrir(exogena), _cert_220("900111222", 85_000_000, retencion=8_000_000))
    )
    partidas = [
        resolver(
            p,
            Decision.USAR_OTRO,
            motivo=Motivo.DECISION_DEL_CONTADOR,
            quien="contador@x.co",
            valor=0,
        )
        if p.concepto is Concepto.RETENCION
        else p
        for p in partidas
    ]
    caso = _a_caso(partidas)
    assert caso.laborales[0].retencion == 0  # la decisión del contador rige
    assert any(f.codigo == "RETENCION_DESPLAZADA" for f in avisos(partidas))


def test_la_retencion_explicita_igual_a_la_certificada_no_avisa():
    """Desplazar la MISMA cifra no le dice nada al contador: sin aviso (el escenario
    16M/8M de la herencia, donde las dos fuentes dicen 8M)."""
    partidas = _partidas_laborales_completas()
    assert not any(f.codigo == "RETENCION_DESPLAZADA" for f in avisos(partidas))


def test_la_suelta_sin_nit_resuelta_junto_a_la_conciliada_avisa_doble_conteo():
    """I6 de la ronda 2 (el dato que faltaba en el concern 4): si una persona resuelve
    con hecho la suelta sin NIT Y la conciliada del mismo empleador, entran 170M donde
    hay 85M — y también SE DOBLA LA RETENCIÓN (los mismos 16M blindados por el otro
    camino, entrando por este). El ensamble es el único punto donde se ven todas las
    resueltas juntas: mismo concepto + misma cifra + mismo nombre, una con NIT y otra
    sin, deja aviso."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    partidas = incorporar(
        partidas, _cert_220_completo("c", nit="", aportes_salud=0, aportes_pension=0)
    )
    partidas = incorporar(partidas, _cert_220_completo("d", aportes_salud=0, aportes_pension=0))
    partidas = autorresolver(partidas)
    partidas = [
        p
        if p.resolucion is not None
        else resolver(
            p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )  # el error humano que el aviso ataja
        for p in partidas
    ]
    [aviso] = [f for f in avisos(partidas) if f.codigo == "POSIBLE_DOBLE_CONTEO"]
    assert "sin-nit:CERT_INGRESOS_220:SALARIOS" in aviso.mensaje
    assert "900111222:SALARIOS" in aviso.mensaje
    assert "$85.000.000" in aviso.mensaje


def test_cifras_distintas_no_disparan_el_aviso_de_doble_conteo():
    """La heurística es barata a propósito: con cifras distintas no afirma nada."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    partidas = incorporar(
        partidas,
        _cert_220_completo("c", nit="", salarios=60_000_000, aportes_salud=0, aportes_pension=0),
    )
    partidas = incorporar(partidas, _cert_220_completo("d", aportes_salud=0, aportes_pension=0))
    partidas = autorresolver(partidas)
    partidas = [
        p
        if p.resolucion is not None
        else resolver(
            p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co"
        )
        for p in partidas
    ]
    assert not any(f.codigo == "POSIBLE_DOBLE_CONTEO" for f in avisos(partidas))


def test_la_suelta_cerrada_sin_soporte_no_dispara_el_aviso():
    """La salida documentada (CERRAR_SIN_SOPORTE sobre la suelta) apaga la heurística:
    la suelta ya no aporta hecho y no hay doble conteo que avisar."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    partidas = incorporar(
        partidas, _cert_220_completo("c", nit="", aportes_salud=0, aportes_pension=0)
    )
    partidas = incorporar(partidas, _cert_220_completo("d", aportes_salud=0, aportes_pension=0))
    partidas = autorresolver(partidas)
    partidas = [
        p
        if p.resolucion is not None
        else resolver(
            p,
            Decision.CERRAR_SIN_SOPORTE,
            motivo=Motivo.DECISION_DEL_CONTADOR,
            quien="contador@x.co",
        )
        for p in partidas
    ]
    assert not any(f.codigo == "POSIBLE_DOBLE_CONTEO" for f in avisos(partidas))


def test_marcar_ajeno_no_excluye_en_silencio():
    """I7 de la ronda 2, ruling: 'la exclusión jamás silenciosa' aplica a las TRES
    decisiones sin hecho. MARCAR_AJENO sobre una partida que la DIAN reportó AL TITULAR
    era la puerta paralela: 85M excluidos con avisos()==[] y nada en el borrador. Aviso
    informativo con partida, tercero, concepto, cifra y motivo; bloqueante queda solo
    para LLEVAR_A_MANO, donde la plata sí es del contribuyente."""
    [p] = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    resuelta = resolver(p, Decision.MARCAR_AJENO, motivo=Motivo.NO_ES_MIO, quien="contador@x.co")
    caso = _a_caso([resuelta])
    assert caso.ingresos_brutos_totales == 0
    [aviso] = avisos([resuelta])
    assert aviso.codigo == "INGRESO_EXCLUIDO"
    assert aviso.severidad == "info"
    assert "900111222:SALARIOS" in aviso.mensaje
    assert "ACME SAS" in aviso.mensaje
    assert "$85.000.000" in aviso.mensaje
    assert "MARCAR_AJENO" in aviso.mensaje
    assert "NO_ES_MIO" in aviso.mensaje


def test_cerrar_sin_soporte_tambien_enumera_lo_excluido():
    p = resolver(
        partida_concepto_desconocido(),
        Decision.CERRAR_SIN_SOPORTE,
        motivo=Motivo.DECISION_DEL_CONTADOR,
        quien="contador@x.co",
    )
    [aviso] = avisos([p])
    assert aviso.codigo == "INGRESO_EXCLUIDO"
    assert aviso.severidad == "info"
    assert "$5.000.000" in aviso.mensaje
    assert "sin clasificar" in aviso.mensaje  # concepto None: se dice, no se inventa


# ─────────── el patrimonio que la exogena si reporta llega al 210 ───────────
#
# La DIAN reporta los saldos bancarios, las cesantias acumuladas y los activos laborales, y los
# manda a "R29 Patrimonio Bruto". Son patrimonio de verdad: si el ensamble los ignora, la
# casilla 29 sale sin ellos y la comparacion patrimonial —la validacion que protege al
# declarante de un requerimiento— se hace contra un patrimonio incompleto.


def _patrimonial(nit, detalle, monto, uso, nombre="BANCO ACME"):
    from tests.unit.conciliacion.test_cruce import _fila_libre

    return _fila_libre(nit, detalle, monto, uso, nombre=nombre)


def test_un_saldo_bancario_llega_al_patrimonio_del_caso():
    from tests.unit.conciliacion.test_cruce import _exogena

    partidas = autorresolver(
        abrir(
            _exogena(
                _patrimonial(
                    "890903938",
                    "Saldo cuentas bancarias (Titular Principal)",
                    1_998_635,
                    "Tope 2: Patrimonio | R29 Patrimonio Bruto",
                )
            )
        )
    )
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)

    assert [a.valor_31dic for a in caso.patrimonio.activos] == [1_998_635]
    activo = caso.patrimonio.activos[0]
    # Entra como "otro" a proposito: cuando el concepto mapea, la partida se agrupa por
    # `nit:CONCEPTO` y el texto del reporte ya no esta, asi que no hay de donde inferir el tipo.
    # Y no hace falta, porque todo activo suma a R29: el tipo no cambia ninguna casilla.
    assert activo.tipo == "otro"
    # La descripcion si dice de donde salio: es lo que se lee en la memoria de cálculo.
    assert "BANCO ACME" in activo.descripcion


def test_una_cuenta_por_pagar_llega_como_deuda():
    from tests.unit.conciliacion.test_cruce import _exogena

    partidas = autorresolver(
        abrir(_exogena(_patrimonial("900111222", "Cuentas por pagar", 2_329_746, "R30 Deudas")))
    )
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025)

    assert [d.saldo_31dic for d in caso.patrimonio.deudas] == [2_329_746]


def test_el_patrimonio_capturado_a_mano_se_suma_al_reportado_no_lo_reemplaza():
    """La exogena trae saldos y cesantias; el carro y la casa los captura una persona. El 210
    lleva LOS DOS: si el ensamble reemplazara, declarar el carro borraria los saldos."""
    from declaras.caso import Activo, Fuente, Patrimonio
    from tests.unit.conciliacion.test_cruce import _exogena

    partidas = autorresolver(
        abrir(
            _exogena(
                _patrimonial(
                    "890903938", "Saldo cuentas bancarias", 1_998_635, "R29 Patrimonio Bruto"
                )
            )
        )
    )
    a_mano = Patrimonio(
        activos=[
            Activo(
                tipo="vehiculo",
                descripcion="Mazda 3 modelo 2019",
                valor_31dic=45_000_000,
                fuente=Fuente.manual("captura"),
            )
        ]
    )
    caso = a_caso(partidas, contribuyente=CONTRIB, anio_gravable=2025, patrimonio=a_mano)

    tipos = sorted(a.tipo for a in caso.patrimonio.activos)
    assert tipos == ["otro", "vehiculo"], "el carro llega con su tipo; la exógena, sin él"
    assert sum(a.valor_31dic for a in caso.patrimonio.activos) == 46_998_635
