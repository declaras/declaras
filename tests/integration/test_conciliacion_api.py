"""El cable entre las dos mitades del producto, por HTTP.

Recorre el ciclo de vida completo: conciliar la exógena → ver el 210 preliminar → ver qué
documentos pedir → recibirlos de a varios → resolver lo que quedó en discrepancia →
ver la ganancia → cerrar el borrador. Con dobles: el lector del 220 es un doble
determinístico (el real cuesta una llamada a un modelo), todo lo demás es el sistema real.
"""

from __future__ import annotations

import hashlib

import pytest

from declaras.documents import registry
from declaras.documents.models import DocumentReading, ExtractedField
from tests.documents_fixtures import build_exogena_xlsx

DOC_220 = "CERT_INGRESOS_220"
ID_TITULAR = "1000000001"
NOMBRE_TITULAR = "PEREZ GOMEZ ANA MARIA"

_SALARIO_DIAN = 87_400_000
_SALARIO_220 = 85_000_000

FILA_SALARIO = {
    "reporter_nit": "900111222",
    "reporter_name": "ACME SAS",
    "concept": "Salarios (Concepto: 5001)",
    "amount": _SALARIO_DIAN,
    "suggested_use": "Tope 1: Ingresos brutos | R32 Ingresos brutos",
}
FILA_RENDIMIENTOS = {
    "reporter_nit": "890903938",
    "reporter_name": "BANCO DEMO",
    "concept": "Rendimientos financieros (Concepto: 5010)",
    "amount": 8_000_000,
    "suggested_use": "Tope 1: Ingresos brutos | R32 Ingresos brutos",
}
FILA_HONORARIOS = {
    "reporter_nit": "901222333",
    "reporter_name": "ZETA SAS",
    "concept": "Honorarios (Concepto: 5002)",
    "amount": 10_000_000,
    "suggested_use": "Tope 1: Ingresos brutos | R32 Ingresos brutos",
}


def _exogena(*filas: dict) -> bytes:
    return build_exogena_xlsx(
        id_number=ID_TITULAR,
        taxpayer_name=NOMBRE_TITULAR,
        detail_rows=list(filas) or [FILA_SALARIO, FILA_RENDIMIENTOS],
    )


def _bytes_220(
    nit: str = "900111222",
    salarios: int = _SALARIO_220,
    *,
    retencion: int = 8_000_000,
    salud: int = 3_400_000,
    pension: int = 3_400_000,
    nombre: str = "ACME SAS",
) -> bytes:
    """El certificado como bytes: el doble del lector saca las cifras de acá, así que cada
    archivo distinto tiene su propio sha (que es la identidad con que el cruce lo registra)."""
    return f"220|{nit}|{nombre}|{salarios}|{retencion}|{salud}|{pension}".encode()


@pytest.fixture(autouse=True)
def lector_220(monkeypatch):
    """Doble determinístico del lector con modelo del 220."""

    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        _, nit, nombre, salarios, retencion, salud, pension = content.decode().split("|")
        campos = {
            "empleador_nit": nit,
            "empleador_nombre": nombre,
            "salarios": int(salarios),
            "retencion": int(retencion),
            "aportes_salud": int(salud),
            "aportes_pension": int(pension),
        }
        return DocumentReading(
            doc_type=DOC_220,
            parser="doble",
            content_sha256=hashlib.sha256(content).hexdigest(),
            fields=[
                ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()
            ],
        )

    monkeypatch.setitem(registry.LLM_READERS, DOC_220, lector)


async def _abrir_caso(client, *filas: dict) -> str:
    creado = await client.post(
        "/v1/cases",
        json={"id_number": ID_TITULAR, "tax_year": 2025, "full_name": NOMBRE_TITULAR},
    )
    case_id = creado.json()["id"]
    await _subir(client, case_id, "EXOGENA", "exogena.xlsx", _exogena(*filas))
    return case_id


async def _subir(client, case_id: str, doc_type: str, nombre: str, contenido: bytes):
    return await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": doc_type},
        files={"file": (nombre, contenido, "application/octet-stream")},
    )


async def _conciliado(client, *filas: dict) -> str:
    case_id = await _abrir_caso(client, *filas)
    respuesta = await client.post(f"/v1/cases/{case_id}/conciliacion")
    assert respuesta.status_code == 200, respuesta.text
    return case_id


async def _partidas(client, case_id: str) -> list[dict]:
    respuesta = await client.get(f"/v1/cases/{case_id}/conciliacion")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["partidas"]


# ─────────────────────────── 1. conciliar ───────────────────────────


async def test_conciliar_abre_las_partidas_de_la_exogena_con_sus_provisionales(client):
    case_id = await _abrir_caso(client)
    respuesta = await client.post(f"/v1/cases/{case_id}/conciliacion")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 2
    assert cuerpo["por_estado"] == {"SOLO_DIAN": 2}
    # Las provisionales del sistema son lo que hace existir el preliminar sin esperar
    # un solo documento del cliente.
    assert cuerpo["pendientes"] == 0
    partidas = await _partidas(client, case_id)
    assert {p["resolucion"]["origen"] for p in partidas} == {"SISTEMA"}
    assert {p["resolucion"]["decision"] for p in partidas} == {"USAR_DIAN"}


async def test_conciliar_sin_reporte_de_terceros_lo_dice_en_vez_de_reventar(client):
    creado = await client.post("/v1/cases", json={"id_number": ID_TITULAR, "tax_year": 2025})
    case_id = creado.json()["id"]
    respuesta = await client.post(f"/v1/cases/{case_id}/conciliacion")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "SIN_REPORTE_DE_TERCEROS"


async def test_cada_renglon_dice_que_se_puede_decidir_sobre_el(client):
    """La interfaz no puede ofrecer una decisión que el backend rechaza, ni esconder la
    única salida que un renglón tiene. La lista sale del conciliador, no de una copia."""
    case_id = await _conciliado(client, FILA_SALARIO, FILA_HONORARIOS)
    por_id = {p["id"]: p for p in await _partidas(client, case_id)}

    salarios = por_id["900111222:SALARIOS"]["decisiones_posibles"]
    assert set(salarios) == {"USAR_DIAN", "MARCAR_AJENO", "USAR_OTRO"}
    assert salarios["USAR_DIAN"] == ["FALTA_DOCUMENTO", "DECISION_DEL_CONTADOR"] or set(
        salarios["USAR_DIAN"]
    ) >= {"FALTA_DOCUMENTO"}

    # Honorarios: el motor no los liquida, así que acá SÍ aparece la salida manual, con su
    # único motivo. Es el caso que T5 dejó anotado para la interfaz.
    honorarios = por_id["901222333:HONORARIOS"]["decisiones_posibles"]
    assert honorarios["LLEVAR_A_MANO"] == ["FUERA_DEL_MOTOR"]
    assert "LLEVAR_A_MANO" not in salarios


async def test_las_partidas_salen_ordenadas_por_plata_en_juego(client):
    case_id = await _conciliado(client)
    montos = [p["plata_en_juego"] for p in await _partidas(client, case_id)]
    assert montos == sorted(montos, reverse=True)


# ─────────────────────────── 2. el preliminar ───────────────────────────


async def test_el_preliminar_existe_y_todavia_no_hay_ganancia(client):
    case_id = await _conciliado(client)
    respuesta = await client.get(f"/v1/cases/{case_id}/liquidacion")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["preliminar"]["version"] == 1
    assert cuerpo["preliminar"]["impuesto"] > 0
    assert cuerpo["ganancia"] == 0
    assert cuerpo["actual"]["version"] == cuerpo["preliminar"]["version"]


async def test_sin_conciliar_no_hay_liquidacion_que_mostrar(client):
    case_id = await _abrir_caso(client)
    respuesta = await client.get(f"/v1/cases/{case_id}/liquidacion")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"


# ─────────────────────────── 3. las peticiones ───────────────────────────


async def test_las_peticiones_traen_el_220_del_empleador_y_la_prepagada(client):
    case_id = await _conciliado(client)
    respuesta = await client.get(f"/v1/cases/{case_id}/peticiones")
    assert respuesta.status_code == 200, respuesta.text
    peticiones = respuesta.json()
    tipos = {p["tipo_documento"] for p in peticiones}
    assert "CERT_INGRESOS_220" in tipos
    assert "CERT_PREPAGADA" in tipos
    del_220 = next(p for p in peticiones if p["tipo_documento"] == "CERT_INGRESOS_220")
    assert del_220["tercero"]["nit"] == "900111222"
    assert "ACME SAS" in del_220["copy_sugerido"]


# ─────────────────────────── 4. la subida masiva ───────────────────────────


async def test_subir_tres_archivos_devuelve_el_desenlace_de_cada_uno(client):
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": [DOC_220, DOC_220, "CERT_PREPAGADA"]},
        files=[
            ("file", ("220-acme.pdf", _bytes_220(), "application/pdf")),
            (
                "file",
                (
                    "220-otro.pdf",
                    _bytes_220("900999888", 12_000_000, nombre="OTRO EMPLEADOR SAS"),
                    "application/pdf",
                ),
            ),
            ("file", ("prepagada.jpg", b"una foto cualquiera", "image/jpeg")),
        ],
    )
    assert respuesta.status_code == 200, respuesta.text
    resultados = respuesta.json()["resultados"]
    assert [r["archivo"] for r in resultados] == ["220-acme.pdf", "220-otro.pdf", "prepagada.jpg"]
    assert [r["estado"] for r in resultados] == ["emparejado", "sin_emparejar", "a_bandeja"]
    # El expediente sigue devolviéndose completo: el contrato anterior no se rompe.
    assert len(respuesta.json()["documents"]) == 4

    partidas = await _partidas(client, case_id)
    acme = next(p for p in partidas if p["id"] == "900111222:SALARIOS")
    assert acme["estado"] == "DISCREPANCIA"
    assert acme["version_dian"]["monto"] == _SALARIO_DIAN
    assert acme["version_documento"]["monto"] == _SALARIO_220
    # La provisional rancia NO sobrevive al documento nuevo: si sobreviviera, el 210
    # declararía 87,4 millones y la discrepancia jamás llegaría a la cola.
    assert acme["resolucion"] is None


async def test_el_documento_que_llega_con_su_peticion_la_cierra(client):
    case_id = await _conciliado(client)
    peticiones = (await client.get(f"/v1/cases/{case_id}/peticiones")).json()
    del_220 = next(p for p in peticiones if p["tipo_documento"] == "CERT_INGRESOS_220")
    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": DOC_220, "peticion_id": del_220["id"]},
        files=[("file", ("220.pdf", _bytes_220(), "application/pdf"))],
    )
    assert respuesta.status_code == 200, respuesta.text
    [resultado] = respuesta.json()["resultados"]
    assert resultado["peticion_cerrada"] is True


async def test_un_doc_type_por_archivo_o_ninguno(client):
    """Dos archivos y tres tipos es una petición mal armada, no un tipo que se adivina."""
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": [DOC_220, DOC_220, DOC_220]},
        files=[
            ("file", ("a.pdf", _bytes_220(), "application/pdf")),
            ("file", ("b.pdf", _bytes_220("900999888", 1_000_000), "application/pdf")),
        ],
    )
    assert respuesta.status_code == 422
    assert respuesta.json()["code"] == "VALIDATION_ERROR"


# ─────────────────────────── 5. la ganancia ───────────────────────────


async def test_la_ganancia_aparece_cuando_el_220_queda_resuelto(client):
    """El 220 trae los aportes obligatorios (INCRNGO) y la retención. Los aportes abren
    partidas SOLO_DOCUMENTO —la exógena los reporta bajo el NIT de la EPS, no del
    empleador—, así que NO se auto-resuelven: la ganancia aparece cuando el contador
    decide, no al soltar el archivo."""
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    antes = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()

    for partida in await _partidas(client, case_id):
        if partida["resolucion"] is not None:
            continue
        decision = "USAR_DIAN" if partida["estado"] == "SOLO_DIAN" else "USAR_DOCUMENTO"
        motivo = "FALTA_DOCUMENTO" if decision == "USAR_DIAN" else "ERROR_DEL_TERCERO"
        respuesta = await client.post(
            f"/v1/cases/{case_id}/conciliacion/{partida['id']}/resolver",
            json={"decision": decision, "motivo": motivo, "quien": "contador@declaras.co"},
        )
        assert respuesta.status_code == 200, respuesta.text

    despues = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert despues["actual"]["version"] > antes["actual"]["version"]
    assert despues["actual"]["impuesto"] < despues["preliminar"]["impuesto"]
    assert despues["ganancia"] > 0
    assert despues["preliminar"]["version"] == 1


# ─────────────────────────── 6 y 7. resolver ───────────────────────────


async def test_una_decision_que_no_aplica_al_estado_da_409(client):
    case_id = await _conciliado(client)
    # USAR_DOCUMENTO sobre una SOLO_DIAN: no hay documento de dónde tomar la cifra.
    respuesta = await client.post(
        f"/v1/cases/{case_id}/conciliacion/900111222:SALARIOS/resolver",
        json={"decision": "USAR_DOCUMENTO", "motivo": "ERROR_DEL_TERCERO", "quien": "c"},
    )
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "DECISION_NO_APLICA"


async def test_resolver_saca_la_partida_de_la_cola_de_pendientes(client):
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    antes = await _partidas(client, case_id)
    assert "900111222:SALARIOS" in [p["id"] for p in antes if p["resolucion"] is None]

    respuesta = await client.post(
        f"/v1/cases/{case_id}/conciliacion/900111222:SALARIOS/resolver",
        json={
            "decision": "USAR_DOCUMENTO",
            "motivo": "ERROR_DEL_TERCERO",
            "quien": "contador@declaras.co",
            "nota": "El 220 manda: el reporte del tercero venía inflado.",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["partida"]["resolucion"]["origen"] == "CONTADOR"
    pendientes = [p["id"] for p in await _partidas(client, case_id) if p["resolucion"] is None]
    assert "900111222:SALARIOS" not in pendientes


async def test_resolver_una_partida_que_no_existe_da_404(client):
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/conciliacion/no-existe/resolver",
        json={"decision": "USAR_DIAN", "motivo": "FALTA_DOCUMENTO", "quien": "c"},
    )
    assert respuesta.status_code == 404
    assert respuesta.json()["code"] == "PARTIDA_NO_ENCONTRADA"


async def test_llevar_a_mano_un_concepto_que_el_motor_si_liquida_da_409(client):
    """El gate del conciliador llega íntegro por HTTP: excluir un ingreso que sí se
    liquida sería subdeclarar con un gate más débil que el resto de la tabla."""
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/conciliacion/900111222:SALARIOS/resolver",
        json={"decision": "LLEVAR_A_MANO", "motivo": "FUERA_DEL_MOTOR", "quien": "c"},
    )
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "DECISION_NO_APLICA"


# ─────────────────────────── 8 y 9. respuestas y cierre de peticiones ───────────────────────────


async def test_un_no_del_cliente_apaga_la_peticion(client):
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/respuestas",
        json={"pregunta": "PREPAGADA", "tiene": False, "quien": "cliente"},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert all(p["tipo_documento"] != "CERT_PREPAGADA" for p in respuesta.json()["peticiones"])
    peticiones = (await client.get(f"/v1/cases/{case_id}/peticiones")).json()
    assert all(p["tipo_documento"] != "CERT_PREPAGADA" for p in peticiones)


async def test_un_si_del_cliente_convierte_la_pregunta_en_peticion_de_documento(client):
    case_id = await _conciliado(client)
    await client.post(
        f"/v1/cases/{case_id}/respuestas",
        json={
            "pregunta": "PREPAGADA",
            "tiene": True,
            "detalle": {"entidad": "Colsanitas"},
            "quien": "cliente",
        },
    )
    peticiones = (await client.get(f"/v1/cases/{case_id}/peticiones")).json()
    prepagada = next(p for p in peticiones if p["tipo_documento"] == "CERT_PREPAGADA")
    assert prepagada["pregunta_previa"] is None


async def test_cerrar_una_peticion_devuelve_lo_que_cuesta(client):
    case_id = await _conciliado(client)
    peticiones = (await client.get(f"/v1/cases/{case_id}/peticiones")).json()
    prepagada = next(p for p in peticiones if p["tipo_documento"] == "CERT_PREPAGADA")
    respuesta = await client.post(f"/v1/cases/{case_id}/cerrar-peticion/{prepagada['id']}")
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["costo"] == prepagada["ahorro_estimado"]
    assert cuerpo["costo_es_techo"] == prepagada["ahorro_es_techo"]
    assert all(p["id"] != prepagada["id"] for p in cuerpo["peticiones"])


async def test_cerrar_una_peticion_que_no_existe_da_404(client):
    case_id = await _conciliado(client)
    respuesta = await client.post(f"/v1/cases/{case_id}/cerrar-peticion/NO_EXISTE")
    assert respuesta.status_code == 404
    assert respuesta.json()["code"] == "PETICION_NO_ENCONTRADA"


# ─────────────────────────── 10. idempotencia ───────────────────────────


async def test_reconciliar_preserva_la_decision_del_contador_y_repone_las_provisionales(client):
    case_id = await _conciliado(client)
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/890903938:RENDIMIENTOS/resolver",
        json={"decision": "USAR_DIAN", "motivo": "FALTA_DOCUMENTO", "quien": "contador"},
    )
    otra_vez = await client.post(f"/v1/cases/{case_id}/conciliacion")
    assert otra_vez.status_code == 200, otra_vez.text
    assert otra_vez.json()["total"] == 2

    por_id = {p["id"]: p for p in await _partidas(client, case_id)}
    assert por_id["890903938:RENDIMIENTOS"]["resolucion"]["origen"] == "CONTADOR"
    assert por_id["900111222:SALARIOS"]["resolucion"]["origen"] == "SISTEMA"


async def test_reconciliar_sin_cambios_no_agrega_una_version_de_la_liquidacion(client):
    """Una versión por request llenaría la historia de filas idénticas y el número de
    versión —que es lo que separa el preliminar de la de hoy— dejaría de significar nada."""
    case_id = await _conciliado(client)
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    cuerpo = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert cuerpo["actual"]["version"] == 1
    assert cuerpo["preliminar"]["version"] == 1


async def test_reconciliar_no_deja_partidas_duplicadas(client):
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    ids = [p["id"] for p in await _partidas(client, case_id)]
    assert len(ids) == len(set(ids))


async def test_una_resolucion_cuya_partida_desaparece_se_lista_en_vez_de_botarse(client):
    """La segunda salida de `refrescar`. Si se descartara al desempacar, la decisión de una
    persona (y la deducción que sostenía) se perdería sin que nadie se enterara."""
    case_id = await _conciliado(client)
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/890903938:RENDIMIENTOS/resolver",
        json={"decision": "USAR_DIAN", "motivo": "FALTA_DOCUMENTO", "quien": "contador"},
    )
    # La DIAN republica el reporte sin la fila del banco: ese id ya no existe.
    await _subir(client, case_id, "EXOGENA", "exogena-v2.xlsx", _exogena(FILA_SALARIO))
    resumen = await client.post(f"/v1/cases/{case_id}/conciliacion")
    assert resumen.status_code == 200, resumen.text
    huerfanas = resumen.json()["resoluciones_sin_partida"]
    assert [h["id"] for h in huerfanas] == ["890903938:RENDIMIENTOS"]
    assert huerfanas[0]["resolucion"]["origen"] == "CONTADOR"
    estado = (await client.get(f"/v1/cases/{case_id}/conciliacion")).json()
    assert [h["id"] for h in estado["resoluciones_sin_partida"]] == ["890903938:RENDIMIENTOS"]


# ─────────────────────────── borrador, memoria y cierre ───────────────────────────


async def test_el_borrador_y_la_memoria_salen_del_caso_conciliado(client):
    case_id = await _conciliado(client)
    borrador = await client.get(f"/v1/cases/{case_id}/borrador")
    assert borrador.status_code == 200, borrador.text
    assert borrador.headers["content-type"].startswith("text/html")
    assert "Borrador Formulario 210" in borrador.text
    assert NOMBRE_TITULAR in borrador.text

    memoria = await client.get(f"/v1/cases/{case_id}/memoria")
    assert memoria.status_code == 200
    assert memoria.headers["content-type"].startswith("text/markdown")
    assert "Memoria de cálculo" in memoria.text
    assert "IMPUESTO_NETO" in memoria.text


async def test_el_borrador_imprime_el_aviso_bloqueante_del_ingreso_que_falta(client):
    """El único canal: sin la fusión de `avisos()` en `Liquidacion.flags`, un 210 al que
    le falta un ingreso se ve completo."""
    case_id = await _conciliado(client, FILA_SALARIO, FILA_HONORARIOS)
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/901222333:HONORARIOS/resolver",
        json={"decision": "LLEVAR_A_MANO", "motivo": "FUERA_DEL_MOTOR", "quien": "contador"},
    )
    borrador = await client.get(f"/v1/cases/{case_id}/borrador")
    assert borrador.status_code == 200, borrador.text
    assert "INGRESO_LLEVADO_A_MANO" in borrador.text
    assert "bloqueante" in borrador.text
    assert "ZETA SAS" in borrador.text


async def test_no_se_cierra_el_borrador_con_un_aviso_bloqueante_vivo(client):
    case_id = await _conciliado(client, FILA_SALARIO, FILA_HONORARIOS)
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/901222333:HONORARIOS/resolver",
        json={"decision": "LLEVAR_A_MANO", "motivo": "FUERA_DEL_MOTOR", "quien": "contador"},
    )
    respuesta = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "LIQUIDACION_BLOQUEADA"
    assert "INGRESO_LLEVADO_A_MANO" in str(respuesta.json()["details"])


async def test_el_borrador_sin_bloqueantes_si_se_cierra(client):
    case_id = await _conciliado(client)
    respuesta = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["status"] == "DRAFT_READY"


async def test_un_concepto_que_el_motor_no_liquida_bloquea_el_preliminar_con_su_motivo(client):
    """Honorarios sin resolver: el sistema no les pone provisional (esconderlos de la cola
    fue el defecto que T5 cerró), así que el caso no se arma y hay que DECIR por qué."""
    case_id = await _conciliado(client, FILA_SALARIO, FILA_HONORARIOS)
    resumen = (await client.post(f"/v1/cases/{case_id}/conciliacion")).json()
    assert resumen["pendientes"] == 1
    assert resumen["falta_para_liquidar"]
    respuesta = await client.get(f"/v1/cases/{case_id}/liquidacion")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"


# ─────────────────────────── llave de API ───────────────────────────


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", "/conciliacion"),
        ("get", "/conciliacion"),
        ("get", "/peticiones"),
        ("get", "/liquidacion"),
        ("get", "/borrador"),
        ("get", "/memoria"),
    ],
)
async def test_todo_el_router_exige_llave_de_api(client, metodo, ruta):
    respuesta = await getattr(client, metodo)(
        f"/v1/cases/00000000-0000-0000-0000-000000000000{ruta}", headers={"X-API-Key": ""}
    )
    assert respuesta.status_code == 401
    assert respuesta.json()["code"] == "UNAUTHORIZED"
