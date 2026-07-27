import pytest

from declaras.documents.models import DocumentReading, ExtractedField, ExtractedRow
from declaras.services.conciliacion import Concepto, EstadoPartida, Lado, abrir, incorporar


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
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    [p] = incorporar(partidas, _cert_220("901999888", 9_000_000))
    assert p.estado == EstadoPartida.SOLO_DIAN
    assert "otra identificación" in (p.nota or "")


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


def test_incorporar_no_muta_la_lista_de_entrada():
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert partidas[0].estado == EstadoPartida.SOLO_DIAN
    assert partidas[0].version_documento is None
