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
    Decision,
    Motivo,
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
        p if p.resolucion is not None
        else resolver(p, Decision.USAR_DOCUMENTO, motivo=Motivo.DECISION_DEL_CONTADOR,
                      quien="contador@x.co")
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
    partidas = incorporar(abrir(_exogena(fila)),
                          _cert_220("900111222", 85_000_000, retencion=8_000_000))
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
        p if p.resolucion is not None
        else resolver(p, Decision.USAR_DIAN, motivo=Motivo.DECISION_DEL_CONTADOR,
                      quien="contador@x.co")  # el contador reclama la ajena: sí es del titular
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
    exogena = _exogena(_fila("890903938", "5010", 8_000_000, retencion=560_000,
                             nombre="BANCO Y"),
                       _fila("901333555", "5005", 36_000_000, nombre="INMOBILIARIA Z"))
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


def test_honorarios_resueltos_con_hecho_revientan():
    """El motor no cubre independientes: silencio acá sería una cédula que desaparece."""
    partidas = autorresolver(abrir(_exogena(_fila("901222333", "5002", 10_000_000))))
    with pytest.raises(NotImplementedError, match="HONORARIOS"):
        _a_caso(partidas)


def test_cerrar_sin_soporte_no_aporta_hecho():
    p = resolver(partida_concepto_desconocido(), Decision.CERRAR_SIN_SOPORTE,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")
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
    resuelta = resolver(partida_solo_dian(), Decision.USAR_DIAN,
                        motivo=Motivo.DECISION_DEL_CONTADOR, quien="x")
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

    campos = {"empleador_nit": "900111222", "empleador_nombre": "ACME SAS",
              "aportes_salud": 3_400_000}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="a" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    [p] = incorporar([], doc)
    resuelta = resolver(p, Decision.USAR_DOCUMENTO,
                        motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")
    with pytest.raises(ValueError, match="aportes"):
        _a_caso([resuelta])


def test_beneficios_patrimonio_y_creditos_entran_como_llegan():
    beneficios = Beneficios(medicina_prepagada=MontoDeclarado(
        valor=6_000_000, fuente=Fuente.manual("contador@x.co")))
    creditos = Creditos(anticipo_pagado=1_000_000)
    caso = a_caso([], contribuyente=CONTRIB, anio_gravable=2025,
                  beneficios=beneficios, patrimonio=Patrimonio(), creditos=creditos)
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
    p = resolver(partida_discrepancia(), Decision.USAR_OTRO,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co",
                 valor=86_000_000)
    caso = _a_caso([p])
    lab = caso.laborales[0]
    assert lab.salarios == 86_000_000
    assert lab.fuente.celda is None  # el número no salió de ninguna celda
    assert lab.fuente.confianza is None
    assert lab.retencion == 0  # ninguna versión fue escogida; la vía es la partida RETENCION


def test_las_sueltas_sin_nit_del_mismo_documento_se_ensamblan_juntas():
    """El 220 sin NIT abre tres partidas sueltas (salarios + los dos aportes): son el
    mismo certificado y arman UN laboral, no un laboral sin aportes más aportes perdidos."""
    partidas = incorporar([], _cert_220_completo("c", nit=""))
    partidas = [resolver(p, Decision.USAR_DOCUMENTO,
                         motivo=Motivo.DECISION_DEL_CONTADOR, quien="contador@x.co")
                for p in partidas]
    caso = _a_caso(partidas)
    [lab] = caso.laborales
    assert (lab.salarios, lab.aportes_salud, lab.aportes_pension) == \
        (85_000_000, 3_400_000, 3_600_000)
    assert lab.empleador_nit == ""


def test_avisos_sin_nada_que_avisar_esta_vacio():
    assert avisos(autorresolver([partida_coincide()])) == []
