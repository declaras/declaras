import pytest

from declaras.documents.models import DocumentReading, ExtractedField, ExtractedRow
from declaras.services.conciliacion import (
    Concepto,
    EstadoPartida,
    Lado,
    Partida,
    Valor,
    abrir,
    incorporar,
)


def _exogena(*filas: dict) -> DocumentReading:
    return DocumentReading(
        doc_type="EXOGENA", parser="test", content_sha256="a" * 64,
        fields=[ExtractedField(name="id_number", value="1234567")],
        rows=[ExtractedRow(values=f, source=f"A{i}") for i, f in enumerate(filas, 20)],
    )


def _fila(nit, codigo, monto, retencion=0, reportado_a="1234567", nombre="ACME SAS"):
    return {
        "reporter_nit": nit, "reporter_name": nombre,
        "reported_id_number": reportado_a, "reported_name": "PRUEBA",
        "concept": f"X (Concepto: {codigo})", "concept_code": codigo,
        "amount": monto, "retencion": retencion,
        "suggested_use": "Tope 1: Ingresos brutos | R32 Ingresos brutos",
    }


def _cert_220(nit, salarios, retencion=0):
    campos = {"empleador_nit": nit, "empleador_nombre": "ACME SAS",
              "salarios": salarios, "retencion": retencion}
    return DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="b" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )


def test_abrir_deja_todo_en_solo_dian():
    """Fase 1: solo hay DIAN. Nada puede estar conciliado todavía."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000),
                              _fila("890903938", "5010", 8_000_000)))
    assert {p.estado for p in partidas} == {EstadoPartida.SOLO_DIAN}
    assert {p.concepto for p in partidas} == {Concepto.SALARIOS, Concepto.RENDIMIENTOS}


def test_incorporar_un_documento_que_confirma():
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_500)))
    partidas = incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert len(partidas) == 1
    assert partidas[0].estado == EstadoPartida.COINCIDE
    assert partidas[0].concepto == Concepto.SALARIOS


def test_discrepancia_expone_las_dos_versiones():
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert p.estado == EstadoPartida.DISCREPANCIA
    assert p.version_dian.monto == 87_400_000
    assert p.version_documento.monto == 85_000_000
    assert p.diferencia_monto == 2_400_000
    assert p.version_dian.lado is Lado.DIAN


def test_discrepancia_solo_en_la_retencion():
    """Monto igual, retención distinta: sigue siendo discrepancia y se ve aparte."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000, retencion=8_000_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000, retencion=6_000_000))
    assert p.estado == EstadoPartida.DISCREPANCIA
    assert p.diferencia_monto == 0
    assert p.diferencia_retencion == 2_000_000


def test_dos_codigos_del_mismo_concepto_son_una_partida():
    partidas = abrir(_exogena(_fila("901222333", "5002", 10_000_000),
                              _fila("901222333", "5003", 4_000_000)))
    assert len(partidas) == 1
    assert partidas[0].version_dian.monto == 14_000_000
    assert sorted(partidas[0].codigos_crudos) == ["5002", "5003"]


def test_solo_documento_de_un_beneficio_que_la_dian_no_ve():
    """Un certificado que nadie pidió y que la DIAN no puede conocer."""
    partidas = incorporar(abrir(_exogena()), _cert_220("900111222", 85_000_000))
    assert partidas[0].estado == EstadoPartida.SOLO_DOCUMENTO


def test_reportado_a_otra_identificacion_no_se_cruza():
    # DESVIACIÓN DEL BRIEF, autorizada en la ronda 4: el test original exigía UNA sola
    # partida, con el certificado adjunto DENTRO de la ajena. Eso estacionaba el
    # certificado del titular donde nadie lo ve (diferencias forzadas a 0, estado que la
    # tabla de decisiones de T5 no puede resolver con USAR_DOCUMENTO). Ahora el
    # certificado siempre abre su propia partida y la ajena guarda la marca estructural.
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    resultado = incorporar(partidas, _cert_220("901999888", 9_000_000))
    ajena = next(p for p in resultado if p.reportado_a is not None)
    assert ajena.estado == EstadoPartida.SOLO_DIAN
    assert "otra identificación" in (ajena.nota or "")
    assert ajena.version_documento is None  # la fila ajena sigue sin aportar hecho


def test_concepto_desconocido_no_se_asume():
    [p] = abrir(_exogena(_fila("900111222", "9999", 5_000_000)))
    assert p.estado == EstadoPartida.CONCEPTO_DESCONOCIDO
    assert p.concepto is None
    assert p.codigos_crudos == ["9999"]


def test_id_de_partida_es_estable_y_por_concepto():
    ex = _exogena(_fila("900111222", "5001", 87_400_000))
    assert abrir(ex)[0].id == abrir(ex)[0].id == "900111222:SALARIOS"


# ─── Bordes que el cruce tiene que aguantar, además del contrato de arriba ───


def test_el_lado_documento_del_220_suma_todos_los_pagos_laborales():
    """El 5001 de la exógena agrega salarios, prima y cesantías; comparar contra
    solo `salarios` marcaría discrepancia falsa a cualquiera que recibió prima."""
    campos = {"empleador_nit": "900111222", "empleador_nombre": "ACME SAS",
              "salarios": 80_000_000, "cesantias_e_intereses": 3_000_000,
              "prima": 4_000_000, "bonificaciones": 400_000, "retencion": 0}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="e" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    [p] = incorporar(partidas, doc)
    assert p.estado == EstadoPartida.COINCIDE
    assert p.version_documento.monto == 87_400_000


def test_documento_sin_nit_nace_suelto_y_con_nota():
    """Una lectura sin NIT no tiene llave: no se adivina a qué tercero pertenece."""
    campos = {"empleador_nombre": "ACME SAS", "salarios": 85_000_000, "retencion": 0}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="c" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    resultado = incorporar(abrir(_exogena(_fila("900111222", "5001", 85_000_000))), doc)
    assert [p.estado for p in resultado] == [EstadoPartida.SOLO_DIAN,
                                             EstadoPartida.SOLO_DOCUMENTO]
    assert "no se pudo cruzar" in (resultado[1].nota or "")


def test_documento_de_tipo_que_no_se_sabe_cruzar_revienta():
    doc = DocumentReading(doc_type="RUT", parser="test", content_sha256="d" * 64)
    with pytest.raises(ValueError, match="RUT"):
        incorporar([], doc)


def test_fila_de_retencion_no_nace_como_ingreso():
    """El reporte real usa códigos de ingreso (5004) también para retenciones; lo que
    desambigua es el renglón que la propia DIAN asigna (R132). Clasificar por código
    creaba un ingreso fantasma por servicios y el crédito de la retención se perdía."""
    fila = _fila("900333444", "5004", 150_000, nombre="BANCO DEMO")
    fila["concept"] = "Retencion en la fuente (Concepto: 5004)"
    fila["suggested_use"] = "R132 Retenciones año gravable a declarar"
    fila["form_lines"] = [132]
    del fila["retencion"]  # el lector real no emite esa clave
    [p] = abrir(_exogena(fila))
    assert p.concepto is Concepto.RETENCION
    assert p.estado == EstadoPartida.SOLO_DIAN
    assert p.version_dian.monto == 150_000


def test_el_renglon_se_lee_del_uso_sugerido_si_la_fila_no_trae_form_lines():
    """Una lectura vieja o construida a mano puede no traer `form_lines`: el renglón
    también se saca del texto de "Uso declaración Sugerida"."""
    fila = _fila("900333444", "5004", 150_000, nombre="BANCO DEMO")
    fila["suggested_use"] = "R132 Retenciones año gravable a declarar"
    [p] = abrir(_exogena(fila))
    assert p.concepto is Concepto.RETENCION


def test_fila_que_apunta_a_retencion_y_a_ingreso_queda_pendiente():
    """Ambigua de verdad: no se clasifica a la ligera, queda para una persona."""
    fila = _fila("900333444", "5001", 150_000)
    fila["suggested_use"] = "Tope 1: Ingresos brutos | R32 Ingresos brutos | R132 Retenciones"
    [p] = abrir(_exogena(fila))
    assert p.estado == EstadoPartida.CONCEPTO_DESCONOCIDO
    assert p.concepto is None
    assert "a mano" in (p.nota or "")


def test_titular_y_ajena_del_mismo_tercero_tienen_ids_distintos():
    """Dos partidas con el mismo id son indistinguibles para cualquier indexado
    (resoluciones, refrescar, Fuente.conciliacion): una de las dos desaparece según
    el orden del XLSX. El id deriva del mismo discriminante completo que la llave."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 50_000_000),
                              _fila("900111222", "5001", 9_000_000, reportado_a="99999")))
    assert len(partidas) == 2
    assert len({p.id for p in partidas}) == 2
    por_destino = {p.reportado_a: p for p in partidas}
    assert por_destino[None].version_dian.monto == 50_000_000
    assert por_destino["99999"].version_dian.monto == 9_000_000


def test_ajenas_a_personas_distintas_no_se_suman():
    """9M reportados a una cédula y 7M a otra son plata de dos personas distintas:
    no pueden quedar bajo una sola partida (ni una sola resolución)."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999"),
                              _fila("901999888", "5001", 7_000_000, reportado_a="88888")))
    assert len(partidas) == 2
    assert sorted(p.version_dian.monto for p in partidas) == [7_000_000, 9_000_000]
    assert len({p.id for p in partidas}) == 2


def test_conceptos_distintos_sin_codigo_no_se_fusionan():
    """`concept_code` es `str | None` de verdad: dos conceptos sin código del mismo
    tercero no son el mismo hecho y no se pueden sumar."""
    salud = _fila("900111222", "", 3_500_000)
    salud["concept"] = "Aportes obligatorios a salud"
    salud["concept_code"] = None
    consignaciones = _fila("900111222", "", 41_000_000)
    consignaciones["concept"] = "Consignaciones bancarias"
    consignaciones["concept_code"] = None
    partidas = abrir(_exogena(salud, consignaciones))
    assert len(partidas) == 2
    assert {p.estado for p in partidas} == {EstadoPartida.CONCEPTO_DESCONOCIDO}
    assert sorted(p.version_dian.monto for p in partidas) == [3_500_000, 41_000_000]


def test_un_texto_igual_a_un_concepto_no_se_cuela_en_la_partida_mapeada():
    """El espacio de nombres de la clave no mezcla concepto normalizado con texto crudo:
    una fila sin código cuyo texto sea exactamente "SALARIOS" no puede caer en la misma
    partida que el 5001 (quedaría una sola partida cuyo concepto y estado dependen del
    orden, y con concepto=None es lo que a_caso de T5 rechaza)."""
    disfrazada = _fila("900111222", "", 41_000_000)
    disfrazada["concept"] = "SALARIOS"
    disfrazada["concept_code"] = None
    partidas = abrir(_exogena(_fila("900111222", "5001", 50_000_000), disfrazada))
    assert len(partidas) == 2
    assert {p.estado for p in partidas} == {EstadoPartida.SOLO_DIAN,
                                            EstadoPartida.CONCEPTO_DESCONOCIDO}
    assert sorted(p.version_dian.monto for p in partidas) == [41_000_000, 50_000_000]
    assert len({p.id for p in partidas}) == 2


def test_terceros_sin_nit_no_se_fusionan():
    """Dos empresas sin NIT no pueden terminar sumadas bajo el nombre de la primera."""
    a = _fila("", "5001", 10_000_000, nombre="EMPRESA A")
    b = _fila("", "5001", 20_000_000, nombre="EMPRESA B")
    partidas = abrir(_exogena(a, b))
    assert len(partidas) == 2
    assert sorted(p.version_dian.monto for p in partidas) == [10_000_000, 20_000_000]
    assert len({p.id for p in partidas}) == 2


def test_misma_cedula_otro_nombre_queda_marcada_para_confirmar():
    """El parser ya decidió (`reported_to_titular=False`): la cédula es la del titular pero
    el nombre es de otra persona. No puede llegar limpia al contador como si fuera suya."""
    fila = _fila("901999888", "5001", 50_000_000)
    fila["reported_to_titular"] = False
    fila["reported_name"] = "OTRA PERSONA DISTINTA"
    [p] = abrir(_exogena(fila))
    assert p.estado == EstadoPartida.SOLO_DIAN
    assert p.reportado_a == "OTRA PERSONA DISTINTA"
    assert "confirmar" in (p.nota or "")


def test_la_marca_de_ajena_no_vive_en_la_nota():
    """`refrescar` de T5 reescribe `nota` por spec; la marca tiene que sobrevivir eso."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    reescrita = partidas[0].model_copy(
        update={"nota": "los valores cambiaron desde la resolución anterior"}
    )
    resultado = incorporar([reescrita], _cert_220("901999888", 9_000_000))
    ajena = next(p for p in resultado if p.reportado_a is not None)
    assert ajena.estado == EstadoPartida.SOLO_DIAN
    assert ajena.version_documento is None


def test_con_dos_gemelas_ajenas_el_certificado_no_elige_por_orden():
    """Con dos gemelas ajenas, pegarse a la primera del XLSX sería salida dependiente
    del orden. El certificado nace aparte, anotado, las ajenas quedan intactas y TODAS
    conservan la marca de que llegó un certificado que podría corresponderles."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999"),
                              _fila("901999888", "5001", 7_000_000, reportado_a="88888")))
    resultado = incorporar(partidas, _cert_220("901999888", 9_000_000))
    invertido = incorporar(list(reversed(partidas)), _cert_220("901999888", 9_000_000))
    assert len(resultado) == 3
    [nueva] = [p for p in resultado if p.estado == EstadoPartida.SOLO_DOCUMENTO]
    assert nueva.version_documento.monto == 9_000_000
    assert "a mano" in (nueva.nota or "")
    assert all(p.version_documento is None for p in resultado if p.reportado_a is not None)
    assert all(p.documentos_por_cruzar == ["b" * 12]
               for p in resultado if p.reportado_a is not None)
    [nueva_inv] = [p for p in invertido if p.estado == EstadoPartida.SOLO_DOCUMENTO]
    assert nueva_inv == nueva  # el desenlace no depende del orden del XLSX


def test_las_diferencias_de_una_ajena_no_comparan_hechos_de_dos_personas():
    """Guard defensivo del modelo: la fila de la DIAN es de otra persona y el certificado
    es del titular, así que restar esos dos números no mide ninguna discrepancia real — y
    `pendientes` de T5 ordena por esta cifra. El cruce ya no adjunta documentos a una
    ajena (el certificado abre su propia partida); el guard protege a quien construya la
    partida por fuera."""
    p = Partida(
        id="901999888:SALARIOS:reportado-a:99999",
        nit_tercero="901999888",
        nombre_tercero="ACME SAS",
        concepto=Concepto.SALARIOS,
        version_dian=Valor(monto=9_000_000, retencion=8_000_000, lado=Lado.DIAN),
        version_documento=Valor(monto=85_000_000, retencion=2_000_000, lado=Lado.DOCUMENTO),
        estado=EstadoPartida.SOLO_DIAN,
        reportado_a="99999",
    )
    assert p.diferencia_monto == 0
    assert p.diferencia_retencion == 0


def test_con_una_sola_ajena_el_certificado_abre_su_propia_partida():
    """Asimetría que dejó la ronda 2: con DOS ajenas el certificado del titular abría su
    propia partida SOLO_DOCUMENTO declarable; con UNA quedaba estacionado dentro de la
    SOLO_DIAN ajena — diferencias forzadas a 0 (`pendientes` de T5 la ordena última) y un
    estado sobre el que la tabla de decisiones no permite USAR_DOCUMENTO — mientras los
    aportes del mismo 220 sí abrían partida propia: T5 podía armar un IngresoLaboral con
    0 de salario y 7M de aportes. Unificado con el caso >=2 (ruling de la ronda 4): el
    certificado SIEMPRE abre su propia partida."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    resultado = incorporar(partidas, _cert_220("901999888", 85_000_000))
    assert len(resultado) == 2
    ajena = next(p for p in resultado if p.reportado_a is not None)
    assert ajena.estado == EstadoPartida.SOLO_DIAN
    assert ajena.version_documento is None  # nada estacionado donde nadie lo ve
    propia = next(p for p in resultado if p.reportado_a is None)
    assert propia.estado == EstadoPartida.SOLO_DOCUMENTO
    assert propia.id == "901999888:SALARIOS"
    assert propia.version_documento.monto == 85_000_000
    assert "a mano" in (propia.nota or "")


def test_con_una_ajena_el_220_completo_no_pierde_el_salario():
    """El desenlace concreto de la asimetría: `IngresoLaboral` de T5 armado con 0 de
    salario y 7M de aportes. Con la unificación, el salario abre partida declarable
    igual que los aportes."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    resultado = incorporar(partidas, _cert_220_completo("a", nit="901999888"))
    por_concepto = {p.concepto: p for p in resultado if p.reportado_a is None}
    assert set(por_concepto) == {Concepto.SALARIOS, Concepto.APORTES_SALUD,
                                 Concepto.APORTES_PENSION}
    assert por_concepto[Concepto.SALARIOS].estado == EstadoPartida.SOLO_DOCUMENTO
    assert por_concepto[Concepto.SALARIOS].version_documento.monto == 85_000_000


def test_la_ajena_conserva_la_marca_estructural_del_certificado_que_llego():
    """Que llegó un certificado que podría corresponderle no puede vivir en `nota` (T5
    la reescribe por spec — la lección de I5): queda en `documentos_por_cruzar`, que
    sobrevive serialización y reescritura de nota, para que el contador lo cruce a mano."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    resultado = incorporar(partidas, _cert_220("901999888", 85_000_000))
    ajena = next(p for p in resultado if p.reportado_a is not None)
    assert ajena.documentos_por_cruzar == ["b" * 12]
    reescrita = ajena.model_copy(update={"nota": "reescrita por refrescar"})
    assert reescrita.documentos_por_cruzar == ["b" * 12]
    revivida = Partida.model_validate(ajena.model_dump())
    assert revivida == ajena


def test_las_marcas_estructurales_sobreviven_el_viaje_de_ida_y_vuelta():
    """`model_dump()` → `model_validate()` y una reescritura de `nota` no pueden borrar
    ninguna marca estructural: `reportado_a`, `versiones_documento` (con su ORDEN),
    `version_que_rige` y `documentos_por_cruzar`. T5 persiste y refresca sobre ellas."""
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    partidas = incorporar(partidas, _cert_220_completo(
        "a", nit="901999888", salarios=85_000_000, aportes_salud=0, aportes_pension=0))
    partidas = incorporar(partidas, _cert_220_completo(
        "b", nit="901999888", salarios=87_000_000, aportes_salud=0, aportes_pension=0))
    ajena = next(p for p in partidas if p.reportado_a is not None)
    propia = next(p for p in partidas if p.reportado_a is None)
    assert ajena.documentos_por_cruzar == ["a" * 12, "b" * 12]
    assert propia.version_que_rige == "b" * 12
    for p in partidas:
        revivida = Partida.model_validate(p.model_dump())
        assert revivida == p
        assert list(revivida.versiones_documento) == list(p.versiones_documento)
        reescrita = revivida.model_copy(update={"nota": "reescrita por refrescar"})
        assert (reescrita.reportado_a, reescrita.version_que_rige,
                reescrita.documentos_por_cruzar, reescrita.versiones_documento) == (
            p.reportado_a, p.version_que_rige,
            p.documentos_por_cruzar, p.versiones_documento)


def test_nit_con_puntos_y_dv_cruza_con_el_nit_limpio():
    """El NIT de la exógena es texto libre del XLSX ("900.111.222-9"); el del 220 llega
    limpio ("900111222"). Sin normalizar eran dos empleadores y la plata se contaba doble."""
    partidas = abrir(_exogena(_fila("900.111.222-9", "5001", 87_400_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 87_400_000))
    assert p.estado == EstadoPartida.COINCIDE
    assert p.nit_tercero == "900111222"
    assert p.id == "900111222:SALARIOS"


def test_nit_entregado_como_numero_por_openpyxl_se_normaliza():
    partidas = abrir(_exogena(_fila(900111222.0, "5001", 87_400_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 87_400_000))
    assert p.estado == EstadoPartida.COINCIDE
    assert p.id == "900111222:SALARIOS"


def test_la_identificacion_reportada_se_compara_normalizada():
    """"1.234.567" y "1234567" son la misma cédula: no es una fila ajena."""
    [p] = abrir(_exogena(_fila("900111222", "5001", 5_000_000, reportado_a="1.234.567")))
    assert p.reportado_a is None
    assert p.estado == EstadoPartida.SOLO_DIAN


def test_el_220_aporta_tambien_los_aportes_obligatorios():
    """`IngresoLaboral` exige `aportes_salud` y `aportes_pension`: si el conciliador los
    deja dentro del documento, T5 solo puede armar el caso con 0 y la deducción se pierde
    (~2M de impuesto de más). El 220 del empleador es la fuente autoritativa; en la
    exógena la EPS/AFP reporta con su PROPIO NIT, así que esas filas no cruzan por el NIT
    del empleador y corroboran bajo sus propias partidas."""
    campos = {"empleador_nit": "900111222", "empleador_nombre": "ACME SAS",
              "salarios": 85_000_000, "retencion": 8_000_000,
              "aportes_salud": 3_400_000, "aportes_pension": 3_600_000}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="f" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    partidas = incorporar(abrir(_exogena()), doc)
    por_concepto = {p.concepto: p for p in partidas}
    assert set(por_concepto) == {Concepto.SALARIOS, Concepto.APORTES_SALUD,
                                 Concepto.APORTES_PENSION}
    assert por_concepto[Concepto.APORTES_SALUD].version_documento.monto == 3_400_000
    assert por_concepto[Concepto.APORTES_PENSION].version_documento.monto == 3_600_000
    assert por_concepto[Concepto.APORTES_SALUD].estado == EstadoPartida.SOLO_DOCUMENTO
    assert por_concepto[Concepto.APORTES_SALUD].id == "900111222:APORTES_SALUD"


def test_aportes_presentes_pero_en_cero_no_abren_partida():
    """En 0 no hay hecho que perder ni pregunta que hacerle al contador."""
    campos = {"empleador_nit": "900111222", "empleador_nombre": "ACME SAS",
              "salarios": 85_000_000, "retencion": 0,
              "aportes_salud": 0, "aportes_pension": 0}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="f" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    partidas = incorporar(abrir(_exogena()), doc)
    assert [p.concepto for p in partidas] == [Concepto.SALARIOS]


def test_incorporar_dos_veces_el_mismo_documento_es_idempotente():
    """Un reenvío del mismo certificado (mismo sha) reemplaza su aporte, no lo duplica."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_500)))
    doc = _cert_220("900111222", 85_000_000)
    una_vez = incorporar(partidas, doc)
    dos_veces = incorporar(una_vez, doc)
    assert dos_veces == una_vez
    assert dos_veces[0].version_documento.monto == 85_000_000


def test_reincorporar_un_documento_sin_nit_no_duplica_la_plata():
    """El camino sin NIT siempre anexaba: el mismo documento dos veces daba dos partidas
    de 85M = 170M. La partida sin NIT tiene id estable por (doc_type, concepto), así que
    el reenvío empareja por id en vez de anexar."""
    campos = {"empleador_nombre": "ACME SAS", "salarios": 85_000_000, "retencion": 0}
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="c" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )
    resultado = incorporar(incorporar([], doc), doc)
    assert len(resultado) == 1
    assert resultado[0].version_documento.monto == 85_000_000


def test_dos_escaneos_sin_nit_del_mismo_220_no_suman_la_plata():
    """El id sin NIT llevaba el sha: dos escaneos del mismo 220 eran DOS partidas que
    nunca se encontraban — 6.800.000 de aportes donde hay 3.400.000, 170M de salarios,
    cero notas y nada que las vinculara. Sin NIT no se puede distinguir "dos escaneos del
    mismo certificado" de "dos certificados de dos empleadores que no pudimos
    identificar", así que no se adivina (ruling de la ronda 4): la partida es UNA por
    (doc_type, concepto), los documentos quedan como rivales anotados con todas sus
    versiones conservadas, y el contador decide — incluso con cifras iguales, porque
    iguales también podrían ser dos empleadores."""
    partidas = incorporar(abrir(_exogena()), _cert_220_completo("c", nit=""))
    partidas = incorporar(partidas, _cert_220_completo("d", nit=""))
    assert len(partidas) == 3
    por_concepto = {p.concepto: p for p in partidas}
    salud = por_concepto[Concepto.APORTES_SALUD]
    assert salud.version_documento.monto == 3_400_000  # publicada, nunca la suma (6.8M)
    assert set(salud.versiones_documento) == {"c" * 12, "d" * 12}  # ninguna desaparece
    assert "a mano" in (salud.nota or "")
    assert salud.version_que_rige == "d" * 12
    salarios = por_concepto[Concepto.SALARIOS]
    assert salarios.version_documento.monto == 85_000_000  # no 170M
    assert "a mano" in (salarios.nota or "")


def test_mezcla_sin_nit_y_luego_con_nit_no_confirma_la_suelta_en_silencio():
    """Un OCR que mejora: el primer escaneo llega sin NIT y el segundo con él legible.
    El certificado con NIT concilia contra la DIAN; la partida suelta del primer escaneo
    NO se suma ni se confirma sola — queda SOLO_DOCUMENTO con su nota, y una persona la
    ve antes de que su plata entre a ninguna parte. (Nota T5: al agregar por concepto,
    una suelta sin resolver no puede sumarse con la partida conciliada del mismo concepto
    como si fueran dos hechos independientes.)"""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    partidas = incorporar(
        partidas, _cert_220_completo("c", nit="", aportes_salud=0, aportes_pension=0))
    partidas = incorporar(
        partidas, _cert_220_completo("d", aportes_salud=0, aportes_pension=0))
    assert len(partidas) == 2
    conciliada = next(p for p in partidas if p.id == "900111222:SALARIOS")
    assert conciliada.estado == EstadoPartida.COINCIDE
    suelta = next(p for p in partidas if p.id == "sin-nit:CERT_INGRESOS_220:SALARIOS")
    assert suelta.estado == EstadoPartida.SOLO_DOCUMENTO
    assert "no se pudo cruzar" in (suelta.nota or "")


def _cert_220_completo(sha: str, *, nit="900111222", nombre="ACME SAS", salarios=85_000_000,
                       retencion=0, aportes_salud=3_400_000,
                       aportes_pension=3_600_000) -> DocumentReading:
    campos = {"empleador_nit": nit, "empleador_nombre": nombre,
              "salarios": salarios, "retencion": retencion,
              "aportes_salud": aportes_salud, "aportes_pension": aportes_pension}
    if not nit:
        del campos["empleador_nit"]  # como un OCR que no encontró el NIT
    return DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256=sha * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )


def test_el_mismo_220_con_otro_hash_no_duplica_los_aportes():
    """El sha es identidad de BYTES, no de documento: el mismo certificado re-escaneado o
    re-exportado llega con otro hash (el repo lo documenta para las descargas del portal).
    Sumar las dos versiones duplicaba los aportes en silencio —deducción inflada, ~2M de
    impuesto de menos— sin nota y con la procedencia colapsada como si fuera un solo
    documento. El 220 no es acumulable: la cifra publicada nunca es la suma. Y como acá
    las cifras son IGUALES, tampoco hay aviso de rivales: no hay nada que decidir
    (ronda 4)."""
    partidas = incorporar(abrir(_exogena()), _cert_220_completo("a"))
    partidas = incorporar(partidas, _cert_220_completo("b"))
    por_concepto = {p.concepto: p for p in partidas}
    salud = por_concepto[Concepto.APORTES_SALUD]
    assert salud.version_documento.monto == 3_400_000
    assert len(salud.versiones_documento) == 2  # nada desaparece
    assert por_concepto[Concepto.SALARIOS].version_documento.monto == 85_000_000


def test_versiones_con_las_mismas_cifras_no_son_rivales():
    """El caso real más común: el mismo PDF re-exportado — cifras iguales, bytes
    distintos. No hay nada que decidir, así que no se ensucia la cola del contador con
    'hay que decidir cuál rige': sin aviso, sin version_que_rige, y las dos versiones
    conservadas. (Solo aplica con NIT: sin NIT hasta cifras iguales podrían ser dos
    empleadores, y ahí sí se anota — ver el test de los dos escaneos sin NIT.)"""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    partidas = incorporar(
        partidas, _cert_220_completo("a", aportes_salud=0, aportes_pension=0))
    partidas = incorporar(
        partidas, _cert_220_completo("b", aportes_salud=0, aportes_pension=0))
    [p] = partidas
    assert p.estado == EstadoPartida.COINCIDE
    assert p.nota is None
    assert p.version_que_rige is None
    assert set(p.versiones_documento) == {"a" * 12, "b" * 12}


def test_entre_versiones_rivales_rige_la_ultima():
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    partidas = incorporar(partidas, _cert_220_completo(
        "a", salarios=85_000_000, aportes_salud=0, aportes_pension=0))
    partidas = incorporar(partidas, _cert_220_completo(
        "b", salarios=87_400_000, aportes_salud=0, aportes_pension=0))
    [salarios] = [p for p in partidas if p.concepto is Concepto.SALARIOS]
    assert salarios.version_documento.monto == 87_400_000
    assert salarios.estado == EstadoPartida.COINCIDE  # la que rige es la que la DIAN corrobora
    assert "cuál rige" in (salarios.nota or "")


def test_la_huella_de_rivales_es_estructural_y_dice_cual_rigio():
    """Si de dos certificados distintos uno cuadra con la exógena, la DIAN rompe el empate:
    la partida queda COINCIDE (mandarla al contador sería trabajo inventado). Pero el hecho
    "hubo rivales y rigió este" no puede vivir en la nota —texto libre que refrescar de T5
    sobrescribe—: es la huella de auditoría de por qué se declaró esa cifra."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    partidas = incorporar(partidas, _cert_220_completo(
        "a", salarios=85_000_000, aportes_salud=0, aportes_pension=0))
    partidas = incorporar(partidas, _cert_220_completo(
        "b", salarios=87_400_000, aportes_salud=0, aportes_pension=0))
    [p] = [x for x in partidas if x.concepto is Concepto.SALARIOS]
    assert p.estado == EstadoPartida.COINCIDE
    assert p.version_que_rige == "b" * 12
    assert set(p.versiones_documento) == {"a" * 12, "b" * 12}
    reescrita = p.model_copy(
        update={"nota": "los valores cambiaron desde la resolución anterior"}
    )
    assert reescrita.version_que_rige == "b" * 12  # sobrevive al refrescar de T5


def test_sin_rivales_no_hay_version_que_rige():
    [p] = incorporar([], _cert_220("900111222", 85_000_000))
    assert p.version_que_rige is None


def test_la_nota_de_rivales_dice_el_numero_real_de_versiones():
    """Con tres versiones en juego la nota no puede seguir diciendo 'dos': el aviso viejo
    se reemplaza por el vigente, no se acumulan. (Las cifras difieren entre las tres:
    versiones con las mismas cifras no son rivales y no avisan.)"""
    partidas = incorporar(abrir(_exogena()), _cert_220_completo("a", salarios=85_000_000))
    partidas = incorporar(partidas, _cert_220_completo("b", salarios=86_000_000))
    partidas = incorporar(partidas, _cert_220_completo("c", salarios=87_000_000))
    [salarios] = [p for p in partidas if p.concepto is Concepto.SALARIOS]
    assert "llegaron 3 certificados" in (salarios.nota or "")
    assert "llegaron 2" not in (salarios.nota or "")
    assert salarios.version_que_rige == "c" * 12


def test_reincorporar_bytes_ya_vistos_no_cambia_la_cifra_declarada():
    """Retry del job de ingesta, o el cliente vuelve a subir el primer archivo: reprocesar
    bytes YA vistos es no-op. Antes `version_que_rige` era 'el último PROCESADO', no 'el
    último nuevo': re-incorporar el certificado viejo volteaba COINCIDE→DISCREPANCIA y
    declaraba 85M donde la DIAN y el certificado vigente dicen 87.4M — la cifra declarada
    cambiaba sin que llegara ningún documento nuevo (y al revés, re-auto-cerraba)."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    viejo = _cert_220_completo("a", salarios=85_000_000, aportes_salud=0, aportes_pension=0)
    nuevo = _cert_220_completo("b", salarios=87_400_000, aportes_salud=0, aportes_pension=0)
    tras_nuevo = incorporar(incorporar(partidas, viejo), nuevo)
    reenvio = incorporar(tras_nuevo, viejo)
    assert reenvio == tras_nuevo  # no-op de verdad, sobre todas las partidas
    [p] = reenvio
    assert p.estado == EstadoPartida.COINCIDE
    assert p.version_documento.monto == 87_400_000
    assert p.version_que_rige == "b" * 12


def test_el_orden_de_llegada_queda_en_las_claves_de_versiones_documento():
    """`refrescar` de T5 tiene que poder distinguir 'versión nueva' (sha que no estaba en
    `versiones_documento`) de 'reenvío viejo' (sha ya visto: no-op). Como el reenvío no
    toca el dict, el orden de las claves es el orden de llegada real y la última clave es
    la versión más nueva — Python no reordena al sobrescribir, así que sin el no-op esta
    historia no existía."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    viejo = _cert_220_completo("a", salarios=85_000_000, aportes_salud=0, aportes_pension=0)
    nuevo = _cert_220_completo("b", salarios=87_400_000, aportes_salud=0, aportes_pension=0)
    [p] = incorporar(incorporar(incorporar(partidas, viejo), nuevo), viejo)
    assert list(p.versiones_documento) == ["a" * 12, "b" * 12]
    assert p.version_que_rige == "b" * 12  # la última NUEVA, no la última procesada


def test_un_reenvio_viejo_en_cualquier_posicion_no_cambia_el_desenlace():
    """Idempotencia con mezclas: el mismo documento repetido entre y después de un rival,
    en varias permutaciones — el resultado es el de la secuencia sin reenvíos."""
    base = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    a = _cert_220_completo("a", salarios=85_000_000, aportes_salud=0, aportes_pension=0)
    b = _cert_220_completo("b", salarios=87_400_000, aportes_salud=0, aportes_pension=0)

    def secuencia(*docs):
        partidas = base
        for doc in docs:
            partidas = incorporar(partidas, doc)
        return partidas

    limpio = secuencia(a, b)
    assert secuencia(a, a, b) == limpio
    assert secuencia(a, b, a) == limpio
    assert secuencia(a, a, b, a, a) == limpio


def test_un_tipo_acumulable_suma_documentos_distintos_sin_importar_el_orden(monkeypatch):
    """La suma por sha sigue existiendo, pero solo para tipos declarados acumulables
    (un banco emite un certificado por CDT y la exógena trae el agregado). El 220 no lo
    es: dos 220 del mismo empleador no tienen caso legítimo de suma."""
    from declaras.services.conciliacion import cruce

    clave = cruce._ClaveDocumento(
        concepto=Concepto.RENDIMIENTOS, campo_nit="banco_nit", campo_nombre="banco_nombre",
        campos_monto=("rendimientos",), campo_retencion="retencion", acumulable=True,
    )
    monkeypatch.setitem(cruce.TIPO_A_CLAVE, "CERT_BANCARIO_TEST", (clave,))

    def cert(sha: str, monto: int) -> DocumentReading:
        campos = {"banco_nit": "890903938", "banco_nombre": "BANCO X",
                  "rendimientos": monto, "retencion": 0}
        return DocumentReading(
            doc_type="CERT_BANCARIO_TEST", parser="test", content_sha256=sha * 64,
            fields=[ExtractedField(name=k, value=v) for k, v in campos.items()],
        )

    a, b = cert("a", 40_000_000), cert("b", 30_000_000)
    base = abrir(_exogena(_fila("890903938", "5010", 70_000_000)))
    ab = incorporar(incorporar(base, a), b)
    ba = incorporar(incorporar(base, b), a)
    assert ab == ba
    [p] = ab
    assert p.version_documento.monto == 70_000_000
    assert p.estado == EstadoPartida.COINCIDE
    assert p.nota is None


def test_sin_nit_cada_version_lleva_su_nombre_y_la_partida_muestra_el_publicado():
    """El ruling de F1 le pide al contador decidir 'mismo certificado repetido o dos
    terceros distintos' — y el nombre del tercero es el único dato con que puede
    decidirlo. La partida mostraba el nombre del PRIMER documento con la cifra del
    último ('ACME S.A.S. — 30.000.000' cuando el certificado de ACME dice 50M, y BETA
    LTDA no aparecía en ninguna parte). El nombre viaja POR VERSIÓN (`Valor.tercero`) y
    la partida se presenta con el de la versión publicada."""
    a = _cert_220_completo("a", nit="", nombre="ACME S.A.S.", salarios=50_000_000,
                           aportes_salud=0, aportes_pension=0)
    b = _cert_220_completo("b", nit="", nombre="BETA LTDA", salarios=30_000_000,
                           aportes_salud=0, aportes_pension=0)
    [p] = incorporar(incorporar([], a), b)
    assert p.versiones_documento["a" * 12].tercero == "ACME S.A.S."
    assert p.versiones_documento["b" * 12].tercero == "BETA LTDA"
    assert p.version_documento.monto == 30_000_000
    assert p.nombre_tercero == "BETA LTDA"  # el nombre acompaña a la cifra publicada


def test_un_tipo_acumulable_sin_nit_no_suma_documentos(monkeypatch):
    """La agregación acumulable presupone lo ÚNICO que el camino sin NIT no tiene: saber
    que los documentos son del mismo tercero. Dos escaneos sin NIT del mismo certificado
    bancario de 40M publicaban 80M —con la nota hablando solo del NIT faltante y
    version_que_rige=None—: F1 literal por la rama `acumulable`, que se evaluaba antes
    que la de rivales. Sin NIT manda el ruling de F1: rivales anotados, nunca suma."""
    from declaras.services.conciliacion import cruce

    clave = cruce._ClaveDocumento(
        concepto=Concepto.RENDIMIENTOS, campo_nit="banco_nit", campo_nombre="banco_nombre",
        campos_monto=("rendimientos",), campo_retencion="retencion", acumulable=True,
    )
    monkeypatch.setitem(cruce.TIPO_A_CLAVE, "CERT_BANCARIO_TEST", (clave,))

    def cert(sha: str) -> DocumentReading:
        campos = {"banco_nombre": "BANCO X", "rendimientos": 40_000_000, "retencion": 0}
        return DocumentReading(
            doc_type="CERT_BANCARIO_TEST", parser="test", content_sha256=sha * 64,
            fields=[ExtractedField(name=k, value=v) for k, v in campos.items()],
        )

    [p] = incorporar(incorporar([], cert("a")), cert("b"))
    assert p.version_documento.monto == 40_000_000  # nunca 80M
    assert set(p.versiones_documento) == {"a" * 12, "b" * 12}  # ninguna desaparece
    assert "a mano" in (p.nota or "")
    assert p.version_que_rige == "b" * 12


def test_los_montos_no_enteros_cierran_por_pesos_no_por_truncamiento():
    """Única mutación que sobrevivió a la ronda 1: int(float(...)) pasaba toda la suite.
    El único punto de redondeo del sistema es dinero.pesos (half-up): 1500.5 sube a 1501,
    un int() lo truncaría a 1500. Se fija en los dos lados."""
    [dian] = abrir(_exogena(_fila("900111222", "5001", 1_500.5)))
    assert dian.version_dian.monto == 1_501
    [doc] = incorporar([], _cert_220("900111222", 1_500.5))
    assert doc.version_documento.monto == 1_501


def test_con_campos_repetidos_gana_el_primero_como_en_field():
    """`DocumentReading.field()` devuelve el PRIMER campo con ese nombre; el conciliador
    tiene que leer el mismo, o el NIT saldría de un campo y el monto de otro."""
    campos = [("empleador_nit", "900111222"), ("empleador_nombre", "ACME SAS"),
              ("salarios", 85_000_000), ("salarios", 1_000_000), ("retencion", 0)]
    doc = DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="9" * 64,
        fields=[ExtractedField(name=k, value=v) for k, v in campos],
    )
    [p] = incorporar([], doc)
    assert p.version_documento.monto == 85_000_000


def test_sin_retencion_reportada_por_la_dian_no_hay_discrepancia_falsa():
    """El XLSX real no tiene columna de retención, así que el lado DIAN no la reporta.
    "No reportada" no es "cero": comparar 0 contra la retención real del 220 mandaba a
    todo asalariado al contador con una discrepancia falsa del tamaño de su retención,
    encabezando la cola de pendientes."""
    fila = _fila("900111222", "5001", 85_000_000)
    del fila["retencion"]  # como el lector real: la clave no existe
    partidas = abrir(_exogena(fila))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000, retencion=8_000_000))
    assert p.estado == EstadoPartida.COINCIDE
    assert p.version_dian.retencion is None
    assert p.diferencia_retencion == 0


def test_retencion_none_del_lector_no_es_un_cero_afirmado():
    """El lector de exógena emite None para celdas ausentes por convención: el día que
    alguien agregue la columna, {"retencion": None} no puede volverse un 0 afirmado que
    reviva la discrepancia falsa que I4 mató."""
    fila = _fila("900111222", "5001", 85_000_000)
    fila["retencion"] = None
    partidas = abrir(_exogena(fila))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000, retencion=8_000_000))
    assert p.version_dian.retencion is None
    assert p.estado == EstadoPartida.COINCIDE


def test_retencion_reportada_en_cero_si_es_comparable():
    """Distinto de la ausente: un lado que afirma 0 sí se compara contra el certificado."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000, retencion=0)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000, retencion=8_000_000))
    assert p.estado == EstadoPartida.DISCREPANCIA
    assert p.diferencia_retencion == 8_000_000


def test_incorporar_no_muta_la_lista_de_entrada():
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert partidas[0].estado == EstadoPartida.SOLO_DIAN
    assert partidas[0].version_documento is None
