"""El cable entre las dos mitades del producto, por HTTP.

Recorre el ciclo de vida completo: conciliar la exógena → ver el 210 preliminar → ver qué
documentos pedir → recibirlos de a varios → resolver lo que quedó en discrepancia →
ver la ganancia → cerrar el borrador. Con dobles: el lector del 220 es un doble
determinístico (el real cuesta una llamada a un modelo), todo lo demás es el sistema real.
"""

from __future__ import annotations

import asyncio
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


async def test_una_cifra_imposible_no_hace_eco_del_validador(client):
    """F3. Una fila de exógena con monto negativo (el lector real las pasa tal cual) revienta
    el modelo del caso con un `ValidationError` de pydantic, que HEREDA de `ValueError`: el
    mensaje crudo del validador salía en el cuerpo 200 de conciliar y en el 409 de la
    liquidación. Nadie que declara tiene que leer "errors.pydantic.dev"."""
    negativa = dict(FILA_RENDIMIENTOS, amount=-5_000_000)
    case_id = await _abrir_caso(client, FILA_SALARIO, negativa)
    resumen = (await client.post(f"/v1/cases/{case_id}/conciliacion")).json()

    falta = resumen["falta_para_liquidar"]
    assert falta is not None
    for interno in ("pydantic", "validation error", "Input should be", "[type="):
        assert interno not in falta
    assert "revisar los renglones" in falta

    negado = await client.get(f"/v1/cases/{case_id}/borrador")
    assert negado.status_code == 409
    assert "pydantic" not in negado.json()["message"]


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


async def test_dos_archivos_con_el_mismo_nombre_reciben_su_propio_desenlace(client):
    """F5. Con el NOMBRE como llave, dos `certificado.pdf` en la misma request recibían los
    dos el desenlace del último: al contador se le decía que el 220 de su empleador no cruzó
    justo cuando acababa de abrir la discrepancia que ahora tiene que decidir. Y el desenlace
    por archivo es el contrato nuevo de esta tarea."""
    case_id = await _conciliado(client)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": [DOC_220, DOC_220]},
        files=[
            ("file", ("certificado.pdf", _bytes_220(), "application/pdf")),
            (
                "file",
                (
                    "certificado.pdf",
                    _bytes_220("900999888", 12_000_000, nombre="OTRA SAS"),
                    "application/pdf",
                ),
            ),
        ],
    )
    assert respuesta.status_code == 200, respuesta.text
    resultados = respuesta.json()["resultados"]
    assert [r["archivo"] for r in resultados] == ["certificado.pdf", "certificado.pdf"]
    assert [r["estado"] for r in resultados] == ["emparejado", "sin_emparejar"]

    acme = next(p for p in await _partidas(client, case_id) if p["id"] == "900111222:SALARIOS")
    assert acme["estado"] == "DISCREPANCIA"
    assert acme["diferencia_monto"] == 2_400_000


async def test_un_archivo_que_no_se_puede_recibir_no_tumba_los_demas(client, monkeypatch):
    """F7. Una excepción no prevista abortaba la request con 500 dejando persistidos los
    archivos anteriores y sin correr el cruce; el cliente reintentaba y duplicaba todo. El
    archivo que falla se reporta como no recibido y los demás siguen su camino."""
    from declaras.services import case_service as modulo

    original = modulo.CaseService.add_client_upload
    llamadas = {"n": 0}

    async def falla_el_segundo(self, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("el disco se cayó a mitad de la subida")
        return await original(self, **kwargs)

    case_id = await _conciliado(client)
    monkeypatch.setattr(modulo.CaseService, "add_client_upload", falla_el_segundo)
    llamadas["n"] = 0

    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": [DOC_220, "CERT_PREPAGADA", "CERT_ICETEX"]},
        files=[
            ("file", ("220.pdf", _bytes_220(), "application/pdf")),
            ("file", ("roto.jpg", b"bytes que revientan", "image/jpeg")),
            ("file", ("icetex.pdf", b"otro certificado", "application/pdf")),
        ],
    )
    assert respuesta.status_code == 200, respuesta.text
    resultados = respuesta.json()["resultados"]
    assert [r["estado"] for r in resultados] == ["emparejado", "no_recibido", "a_bandeja"]
    assert "volver a mandar solo este" in resultados[1]["motivo"]
    # Y el cruce SÍ corrió con lo que entró: la discrepancia del 220 está en la cola.
    acme = next(p for p in await _partidas(client, case_id) if p["id"] == "900111222:SALARIOS")
    assert acme["estado"] == "DISCREPANCIA"


async def test_si_el_cruce_no_alcanza_a_correr_el_archivo_no_se_reenvia(client, monkeypatch):
    """El 409 del chequeo optimista llegaba SOBRE un documento que sí había entrado, con un
    mensaje que pedía "volver a cargarla y repetir": el cliente reintentaba y duplicaba el
    archivo. Se responde 200 con la verdad por archivo — entró, no se cruzó, hay que
    conciliar — y explícitamente que no hay que volverlo a mandar."""
    from declaras.domain.errors import ConflictoDeConcurrenciaError
    from declaras.services import conciliacion_service as modulo

    case_id = await _conciliado(client)

    async def choca(self, case_id, subidos):
        raise ConflictoDeConcurrenciaError()

    monkeypatch.setattr(modulo.ConciliacionService, "incorporar_documentos", choca)
    respuesta = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": DOC_220},
        files=[("file", ("220.pdf", _bytes_220(), "application/pdf"))],
    )
    assert respuesta.status_code == 200, respuesta.text
    [resultado] = respuesta.json()["resultados"]
    assert resultado["estado"] == "a_bandeja"
    assert "no se vuelve a mandar" in resultado["motivo"]
    # Y el documento SÍ quedó guardado, así que reenviarlo lo duplicaría.
    assert len(respuesta.json()["documents"]) == 2


async def test_dos_versiones_con_el_mismo_numero_son_un_conflicto_no_un_500(client, container):
    """`_recalcular` cuenta las versiones y luego inserta: en Postgres dos lecturas
    concurrentes del mismo conteo chocan contra la clave única. Es un conflicto de
    concurrencia (409), no una falla del servidor."""
    from uuid import UUID

    from declaras.domain.errors import ConflictoDeConcurrenciaError

    case_id = await _conciliado(client)
    [primera] = await container.conciliacion.versiones(UUID(case_id))
    with pytest.raises(ConflictoDeConcurrenciaError) as fallo:
        await container.conciliacion.agregar_version(UUID(case_id), primera)
    assert fallo.value.http_status == 409


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


async def test_un_220_deja_un_unico_renglon_por_decidir(client):
    """El ruling del tercer automatismo. Un 220 abre tres renglones: la discrepancia de
    salarios y los dos de aportes obligatorios. Los aportes NUNCA van a cruzar contra la
    exógena —el tercero los reporta bajo el NIT de la EPS o del fondo, no del empleador—,
    así que pedirle una decisión al contador ahí no gana información. El que queda es el
    único que sí necesita criterio: cuál de las dos cifras de salario manda."""
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    partidas = await _partidas(client, case_id)
    pendientes = [p["id"] for p in partidas if p["resolucion"] is None]
    assert pendientes == ["900111222:SALARIOS"]

    aportes = [p for p in partidas if p["concepto"] == "APORTES_SALUD"]
    assert [p["resolucion"]["origen"] for p in aportes] == ["SISTEMA"]
    assert [p["resolucion"]["motivo"] for p in aportes] == ["SIN_CONTRAPARTE_DIAN"]


async def test_la_ganancia_aparece_cuando_el_220_queda_resuelto(client):
    """El 220 trae los aportes obligatorios (INCRNGO) y la retención: la ganancia aparece
    cuando el único renglón que necesita criterio humano —cuál cifra de salario manda—
    queda decidido."""
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


async def test_dos_resoluciones_simultaneas_no_se_pierden_en_silencio(client):
    """F2. Leer el estado, armar la lista completa y reemplazar el bloque entero deja una
    ventana ancha (varias idas a la base y la liquidación completa en medio) en la que la
    segunda escritura borra la decisión de la primera. Las dos respondían 200 y en la base
    quedaba UNA sola decisión: el API afirmaba haber guardado algo que no guardó. Perder la
    decisión de un contador en silencio no es aceptable; la perdedora recibe 409."""
    # Dos empleadores y dos certificados que discrepan: dos renglones DISTINTOS por decidir,
    # que es el escenario del defecto (no hace falta que compitan por el mismo renglón).
    otro = dict(FILA_SALARIO, reporter_nit="900999888", reporter_name="OTRA SAS")
    case_id = await _conciliado(client, FILA_SALARIO, otro)
    await _subir(client, case_id, DOC_220, "acme.pdf", _bytes_220())
    await _subir(
        client, case_id, DOC_220, "otra.pdf", _bytes_220("900999888", nombre="OTRA SAS")
    )
    pendientes = [p["id"] for p in await _partidas(client, case_id) if p["resolucion"] is None]
    assert len(pendientes) >= 2, pendientes

    async def resolver(partida_id: str):
        return await client.post(
            f"/v1/cases/{case_id}/conciliacion/{partida_id}/resolver",
            json={
                "decision": "USAR_DOCUMENTO",
                "motivo": "ERROR_DEL_TERCERO",
                "quien": f"contador-{partida_id}",
            },
        )

    respuestas = await asyncio.gather(
        resolver(pendientes[0]), resolver(pendientes[1]), return_exceptions=True
    )
    codigos = sorted(
        r.status_code for r in respuestas if not isinstance(r, BaseException)
    )
    resueltas = {
        p["id"] for p in await _partidas(client, case_id) if p["resolucion"] is not None
    }

    # O las dos entraron (si la base las serializó), o la perdedora dijo 409 — nunca dos
    # 200 con una sola decisión guardada.
    if codigos == [200, 200]:
        assert {pendientes[0], pendientes[1]} <= resueltas
    else:
        assert codigos == [200, 409]
        assert len({pendientes[0], pendientes[1]} & resueltas) == 1


async def test_la_interfaz_no_ofrece_un_motivo_que_seria_mentira(client):
    """`decisiones_posibles` se deriva de `resolver`, así que la validación nueva de
    estado×motivo la recorta sola: la consola ya no puede pintar "la DIAN no reporta nada"
    sobre un renglón donde la DIAN reporta."""
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    por_id = {p["id"]: p for p in await _partidas(client, case_id)}

    discrepancia = por_id["900111222:SALARIOS"]["decisiones_posibles"]
    assert "SIN_CONTRAPARTE_DIAN" not in discrepancia["USAR_DOCUMENTO"]
    assert "COINCIDEN" not in discrepancia["USAR_DOCUMENTO"]
    rechazado = await client.post(
        f"/v1/cases/{case_id}/conciliacion/900111222:SALARIOS/resolver",
        json={"decision": "USAR_DOCUMENTO", "motivo": "SIN_CONTRAPARTE_DIAN", "quien": "c"},
    )
    assert rechazado.status_code == 409
    assert rechazado.json()["code"] == "DECISION_NO_APLICA"


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


async def test_el_documento_que_llega_antes_de_conciliar_no_borra_la_ganancia(client):
    """F4, y es el ORDEN NATURAL del producto: el cliente manda el certificado por chat y
    el contador concilia después. Si el preliminar fuera "la primera versión que se pudo
    guardar", ya vendría con el 220 dentro y la ganancia saldría 0 — desaparecerían los
    1.311.000 de impuesto y los 10.226.282 de saldo que el producto existe para mostrar.
    La versión 1 se liquida SIEMPRE desde la exógena sola."""
    case_id = await _abrir_caso(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    for partida in await _partidas(client, case_id):
        if partida["resolucion"] is None:
            decision = "USAR_DIAN" if partida["estado"] == "SOLO_DIAN" else "USAR_DOCUMENTO"
            motivo = "FALTA_DOCUMENTO" if decision == "USAR_DIAN" else "ERROR_DEL_TERCERO"
            await client.post(
                f"/v1/cases/{case_id}/conciliacion/{partida['id']}/resolver",
                json={"decision": decision, "motivo": motivo, "quien": "contador"},
            )

    cuerpo = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert cuerpo["preliminar_sin_documentos"] is True
    assert cuerpo["preliminar"]["impuesto"] == 3_661_127
    assert cuerpo["actual"]["impuesto"] == 2_350_127
    assert cuerpo["ganancia"] == 1_311_000
    assert cuerpo["ganancia_saldo"] == 10_226_282


async def test_el_preliminar_dice_cuando_no_pudo_liquidarse_sin_documentos(client):
    """El caso en que la exógena sola NO se puede liquidar (trae honorarios, que el motor
    no cubre y el automatismo no toca): el preliminar cae al primer estado que sí se pudo
    armar, y entonces la ganancia subestima. Se dice, no se disimula."""
    case_id = await _abrir_caso(client, FILA_SALARIO, FILA_HONORARIOS)
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/901222333:HONORARIOS/resolver",
        json={"decision": "LLEVAR_A_MANO", "motivo": "FUERA_DEL_MOTOR", "quien": "contador"},
    )
    cuerpo = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert cuerpo["preliminar_sin_documentos"] is False


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


async def test_una_decision_invalidada_deja_rastro_permanente(client):
    """F6. `refrescar` invalida la decisión del contador con una NOTA, y la nota es texto
    libre que el siguiente rebuild vuelve a derivar desde cero: a la segunda reconstrucción
    desaparecía y quedaba un renglón resuelto por el sistema, fuera de la cola, sin huérfana
    y sin rastro de que una persona había decidido. La plata declarada es la conservadora,
    así que no hay cifra mala — se perdía la auditoría."""
    case_id = await _conciliado(client)
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/890903938:RENDIMIENTOS/resolver",
        json={
            "decision": "USAR_OTRO",
            "motivo": "DECISION_DEL_CONTADOR",
            "valor": 7_777_777,
            "quien": "contador@declaras.co",
        },
    )
    # La DIAN republica la fila del banco con otra cifra: la decisión ya no aplica.
    otra_cifra = dict(FILA_RENDIMIENTOS, amount=9_500_000)
    await _subir(client, case_id, "EXOGENA", "v2.xlsx", _exogena(FILA_SALARIO, otra_cifra))
    await client.post(f"/v1/cases/{case_id}/conciliacion")
    # Y cualquier reconstrucción posterior, que es la que borraba la nota.
    await client.post(f"/v1/cases/{case_id}/conciliacion")

    detalle = (await client.get(f"/v1/cases/{case_id}")).json()
    eventos = [e for e in detalle["events"] if e["kind"] == "RESOLUCION_DESCARTADA"]
    assert len(eventos) == 1, "una vez, ni cero ni una por reconstrucción"
    assert eventos[0]["payload"]["partida_id"] == "890903938:RENDIMIENTOS"
    assert eventos[0]["payload"]["valor"] == 7_777_777
    assert eventos[0]["payload"]["quien"] == "contador@declaras.co"
    alertas = [f for f in detalle["flags"] if f["code"] == "RESOLUCION_DESCARTADA"]
    assert len(alertas) == 1
    assert alertas[0]["resolved_at"] is None


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


async def test_no_se_cierra_un_borrador_que_el_sistema_se_niega_a_imprimir(client):
    """F1, escenario A. El flujo normal: llega el 220 y quedan renglones por decidir, así
    que el caso no se arma y `/borrador` responde 409. Cerrar tiene que negarse por lo
    MISMO: dar por listo un borrador que el propio sistema no imprime, y fechar el evento
    con la cifra de antes del 220, es afirmar que está lista una declaración que no existe."""
    case_id = await _conciliado(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    assert (await client.get(f"/v1/cases/{case_id}/borrador")).status_code == 409

    respuesta = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"
    detalle = (await client.get(f"/v1/cases/{case_id}")).json()
    assert detalle["status"] != "DRAFT_READY"
    assert not [e for e in detalle["events"] if e["kind"] == "DRAFT_READY"]


async def test_un_bloqueante_que_aparece_despues_de_la_ultima_version_igual_bloquea(client):
    """F1, escenario B: el que rompía el requisito 2. La DIAN republica el reporte con
    honorarios → queda 1 pendiente → el caso no se arma → no hay versión nueva. Si `cerrar`
    mira la última versión GUARDADA, el bloqueante nunca se calcula y el borrador se cierra:
    el aviso que existe para impedir exactamente esto no llega a existir."""
    case_id = await _conciliado(client)
    republicada = _exogena(FILA_SALARIO, FILA_HONORARIOS)
    await _subir(client, case_id, "EXOGENA", "exogena-v2.xlsx", republicada)
    resumen = (await client.post(f"/v1/cases/{case_id}/conciliacion")).json()
    assert resumen["pendientes"] == 1

    # Mientras el caso no se arme, cerrar se niega por falta de decisiones...
    primero = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert primero.status_code == 409
    assert primero.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"

    # ...y una vez resuelto (llevado a mano), se niega por el BLOQUEANTE, que ahora sí se
    # calcula sobre el estado de hoy y no sobre una versión vieja sin él.
    await client.post(
        f"/v1/cases/{case_id}/conciliacion/901222333:HONORARIOS/resolver",
        json={"decision": "LLEVAR_A_MANO", "motivo": "FUERA_DEL_MOTOR", "quien": "contador"},
    )
    segundo = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert segundo.status_code == 409
    assert segundo.json()["code"] == "LIQUIDACION_BLOQUEADA"
    assert "INGRESO_LLEVADO_A_MANO" in str(segundo.json()["details"])


async def test_la_liquidacion_dice_cuando_la_version_guardada_ya_no_es_la_de_hoy(client):
    """El colateral de F1: una `actual` rancia sin marca de que lo está es el front
    pintando la cifra pre-220 como "la declaración de hoy", con el 220 ya en la mano."""
    case_id = await _conciliado(client)
    vigente = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert vigente["actual_vigente"] is True
    assert vigente["falta_para_liquidar"] is None

    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    rancia = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert rancia["actual_vigente"] is False
    assert "sin resolver" in rancia["falta_para_liquidar"]


async def test_no_se_cierra_sin_haber_conciliado_nunca(client):
    """Puerta B del Critical de la ronda 2, y es la más barata: `a_caso([])` es válido, así
    que con CERO renglones el guard fallaba ABIERTO y se cerraba un 210 con impuesto 0
    teniendo dentro una exógena de 95 millones y un 220. Un caso vacío solo es un caso
    válido si el expediente está vacío de verdad."""
    case_id = await _abrir_caso(client)
    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    assert await _partidas(client, case_id) == []

    for ruta in ("borrador", "memoria"):
        negado = await client.get(f"/v1/cases/{case_id}/{ruta}")
        assert negado.status_code == 409, ruta
        assert negado.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"
    cerrar = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert cerrar.status_code == 409
    assert (await client.get(f"/v1/cases/{case_id}")).json()["status"] != "DRAFT_READY"


async def test_un_expediente_vacio_tampoco_produce_un_210_en_cero(client):
    """La otra mitad de puerta B: sin NINGÚN documento, `a_caso([])` sigue siendo válido. Un
    210 en cero no es el resultado de una declaración que nadie ha empezado."""
    creado = await client.post("/v1/cases", json={"id_number": ID_TITULAR, "tax_year": 2025})
    case_id = creado.json()["id"]
    for ruta in ("borrador", "memoria", "liquidacion"):
        negado = await client.get(f"/v1/cases/{case_id}/{ruta}")
        assert negado.status_code == 409, ruta
    assert (await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")).status_code == 409


async def test_un_documento_que_entro_por_otra_puerta_invalida_los_renglones(client, container):
    """Puerta A del Critical: `POST /link-extraction` (y cualquier otro camino que meta
    documentos sin pasar por el cruce) dejaba los renglones viejos y todo el mundo los
    trataba como los de hoy. `actual_vigente: true` y `falta_para_liquidar: null` mentían, y
    se daba por bueno un 210 que omite 10.000.000 que la DIAN ya reporta — el escenario B de
    F1 entrando por la puerta de al lado.

    La vigencia se medía contra los RENGLONES PERSISTIDOS, que es la misma suposición que F1
    arregló un nivel más arriba."""
    from uuid import UUID

    case_id = await _conciliado(client)
    vigente = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert vigente["actual_vigente"] is True

    # La DIAN republica el reporte CON honorarios, por un camino que no corre el cruce.
    await container.case_service.add_client_upload(
        case_id=UUID(case_id),
        doc_type="EXOGENA",
        content=_exogena(FILA_SALARIO, FILA_HONORARIOS),
        filename="exogena-republicada.xlsx",
    )

    rancia = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert rancia["actual_vigente"] is False
    assert rancia["falta_para_liquidar"]
    for ruta in ("borrador", "memoria"):
        assert (await client.get(f"/v1/cases/{case_id}/{ruta}")).status_code == 409, ruta
    cerrar = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert cerrar.status_code == 409
    assert cerrar.json()["code"] == "LIQUIDACION_NO_DISPONIBLE"
    assert (await client.get(f"/v1/cases/{case_id}")).json()["status"] != "DRAFT_READY"

    # Y conciliar vuelve a poner los cuatro caminos de acuerdo con el expediente.
    resumen = (await client.post(f"/v1/cases/{case_id}/conciliacion")).json()
    assert resumen["pendientes"] == 1


async def test_un_certificado_que_entro_por_otra_puerta_tambien_invalida(client, container):
    """La otra mitad de puerta A: no basta con vigilar la exógena. Un 220 que entra por un
    camino que no corre el cruce cambia lo que el cruce produciría —abre la discrepancia de
    salarios y los aportes— y dejaba los renglones viejos pasando por vigentes."""
    from uuid import UUID

    case_id = await _conciliado(client)
    assert (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()["actual_vigente"]

    await container.case_service.add_client_upload(
        case_id=UUID(case_id),
        doc_type=DOC_220,
        content=_bytes_220(),
        filename="220-por-otra-puerta.pdf",
    )

    cuerpo = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()
    assert cuerpo["actual_vigente"] is False
    assert (await client.get(f"/v1/cases/{case_id}/borrador")).status_code == 409
    assert (await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")).status_code == 409


async def test_no_se_cierra_si_alguien_resolvio_entre_la_revision_y_el_cierre(
    client, container, monkeypatch
):
    """La carrera `cerrar || resolver` dejaba `DRAFT_READY` con una versión POSTERIOR al
    cierre: el evento fechaba una cifra que ya no era la de la declaración. Se prueba el
    GUARD, no el planificador: la revisión se mueve entre que `cerrar` lee el estado y va a
    cerrar, que es exactamente lo que pasa cuando otra request resuelve un renglón."""
    case_id = await _conciliado(client)
    original = type(container.conciliacion).revision
    llamadas = {"n": 0}

    async def se_mueve(self, cid):
        llamadas["n"] += 1
        actual = await original(self, cid)
        # La primera lectura es la del estado que `cerrar` va a aprobar; la segunda es la
        # del guard, y para entonces alguien más ya escribió.
        return actual if llamadas["n"] == 1 else actual + 1

    monkeypatch.setattr(type(container.conciliacion), "revision", se_mueve)
    respuesta = await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "CONFLICTO_DE_CONCURRENCIA"
    assert (await client.get(f"/v1/cases/{case_id}")).json()["status"] != "DRAFT_READY"


async def test_cerrar_y_resolver_a_la_vez_no_fechan_un_cierre_con_una_cifra_vieja(client):
    """La misma carrera, de verdad y sin suponer quién gana: si queda un borrador dado por
    listo, la versión que el cierre fechó tiene que ser la última — un cierre que apunta a
    una versión anterior a la que hay es un "listo" sobre una cifra que ya nadie declara."""
    case_id = await _conciliado(client)

    async def cerrar():
        return await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")

    async def corregir():
        return await client.post(
            f"/v1/cases/{case_id}/conciliacion/890903938:RENDIMIENTOS/resolver",
            json={
                "decision": "USAR_OTRO",
                "motivo": "DECISION_DEL_CONTADOR",
                "valor": 1_000_000,
                "quien": "contador@declaras.co",
            },
        )

    await asyncio.gather(cerrar(), corregir(), return_exceptions=True)

    detalle = (await client.get(f"/v1/cases/{case_id}")).json()
    if detalle["status"] != "DRAFT_READY":
        return  # el cierre se negó o se invalidó: nada que afirmar
    cerrado = [e for e in detalle["events"] if e["kind"] == "DRAFT_READY"][-1]
    actual = (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()["actual"]
    assert cerrado["payload"]["version"] == actual["version"], (
        "el cierre quedó fechado con una versión que ya no es la de la declaración"
    )
    assert cerrado["payload"]["impuesto"] == actual["impuesto"]


async def test_resolver_un_renglon_no_borra_la_marca_de_que_hay_que_conciliar(client, container):
    """Resolver no re-deriva el cruce, así que tampoco puede volver a sellar los renglones
    con los documentos de hoy: eso borraría la marca de que hay algo sin cruzar y devolvería
    el agujero por la puerta de atrás."""
    from uuid import UUID

    case_id = await _conciliado(client)
    await container.case_service.add_client_upload(
        case_id=UUID(case_id), doc_type=DOC_220, content=_bytes_220(), filename="fuera.pdf"
    )
    assert (await client.get(f"/v1/cases/{case_id}/borrador")).status_code == 409

    await client.post(
        f"/v1/cases/{case_id}/conciliacion/890903938:RENDIMIENTOS/resolver",
        json={"decision": "USAR_DIAN", "motivo": "FALTA_DOCUMENTO", "quien": "contador"},
    )
    assert (await client.get(f"/v1/cases/{case_id}/borrador")).status_code == 409
    assert (await client.get(f"/v1/cases/{case_id}/liquidacion")).json()["actual_vigente"] is False


async def test_cerrar_deja_de_valer_cuando_los_renglones_cambian(client):
    """`DRAFT_READY` era terminal de hecho: nada lo invalidaba. Un borrador "listo" que ya
    no corresponde al expediente es la misma mentira, persistida en el estado."""
    case_id = await _conciliado(client)
    assert (await client.post(f"/v1/cases/{case_id}/liquidacion/cerrar")).status_code == 200
    assert (await client.get(f"/v1/cases/{case_id}")).json()["status"] == "DRAFT_READY"

    await _subir(client, case_id, DOC_220, "220.pdf", _bytes_220())
    detalle = (await client.get(f"/v1/cases/{case_id}")).json()
    assert detalle["status"] == "READY_FOR_REVIEW"
    assert [e for e in detalle["events"] if e["kind"] == "DRAFT_READY_INVALIDADO"]


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
