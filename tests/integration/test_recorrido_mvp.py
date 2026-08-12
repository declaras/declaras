"""El recorrido completo del producto, de abrir el expediente a cerrar el borrador.

═══ QUE PRUEBA ESTO QUE NO PRUEBE EL RESTO ═══

Las demas pruebas de integracion cubren una pieza cada una: la extraccion, el expediente, la
conciliacion. Todas pasaban mientras el recorrido REAL se rompia en produccion, porque lo que
fallaba no era ninguna pieza sino el encadenamiento — y sobre todo el encadenamiento cuando
falta un documento, que es el caso normal y no el excepcional.

Cada prueba de aca es una PARADA del recorrido, en orden, y la que importa es la segunda
tanda: el contribuyente primerizo. El primer usuario real del producto no tenia declaracion
anterior ni borrador de la DIAN (verificado contra el portal el 2026-08-08: responde 404 con
`Documentos no encontrados`), asi que bajaron tres documentos de cinco. Todo lo que sigue tiene
que sostenerse con eso, y lo que NO puede pasar es que una pieza se quede muda: una pantalla
vacia sin explicacion es indistinguible de "no hay diferencias", y son cosas opuestas.

═══ POR QUE CON EL CONECTOR FALSO ═══

Porque el portal real necesita la clave de una persona y no se puede correr en CI. El conector
falso reproduce las mismas ramas —incluido `sindecl`, que copia la respuesta literal de la
DIAN— asi que lo que se ejercita es el mismo codigo desde la API hacia adentro.
"""

from __future__ import annotations

import pytest

from tests.conftest import wait_for_status

CEDULA = "1004683364"
ANIO = 2025

# Las dos comparaciones dependen de documentos que un primerizo no tiene. Van juntas porque el
# defecto que se cuida es el mismo en las dos.
COMPARACIONES = ["comparacion-con-la-dian", "comparacion-con-lo-presentado"]

# Las paradas del recorrido, en el orden en que las recorre una persona.
PARADAS = ("summary", "conciliacion", "liquidacion", "formulario", "recomendaciones")


async def _parada_responde_o_se_explica(client, case_id: str, ruta: str) -> None:
    """La regla de todas las paradas: o entrega el dato, o dice por que no puede.

    NO se exige 200. Con documentos de prueba que no se pueden leer no hay nada que liquidar, y
    negarse es lo correcto. Lo que se exige es que la negativa venga con un codigo estable y una
    frase que explique — porque el defecto que se cuida no es que falte el dato, es que la
    pantalla quede muda. Un 500, o un 409 con el cuerpo vacio, dejan a quien mira sin saber si el
    resultado es "no hay diferencias" o "esto ni se calculo".
    """
    respuesta = await client.get(f"/v1/cases/{case_id}/{ruta}")
    if respuesta.status_code == 200:
        return
    assert respuesta.status_code == 409, f"{ruta} respondió {respuesta.status_code}"
    cuerpo = respuesta.json()
    assert cuerpo.get("code"), f"{ruta} se negó sin código estable"
    assert len(cuerpo.get("message", "")) > 20, f"{ruta} se negó sin explicar: {cuerpo}"


async def _recorrer(client, *, clave: str) -> str:
    """Abre el expediente, corre la extraccion y la vincula. Devuelve el id del expediente."""
    creado = await client.post("/v1/cases", json={"id_number": CEDULA, "tax_year": ANIO})
    assert creado.status_code == 201, creado.text
    case_id = creado.json()["id"]

    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": CEDULA, "dian_password": clave, "tax_year": ANIO},
    )
    job_id = extraccion.json()["job_id"]
    await wait_for_status(client, job_id, "SUCCEEDED", "FAILED")

    vinculado = await client.post(f"/v1/cases/{case_id}/link-extraction", json={"job_id": job_id})
    assert vinculado.status_code == 200, vinculado.text
    return case_id


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Caso 1: el contribuyente completo. Es la linea base.
# ═══════════════════════════════════════════════════════════════════════════════════════════


async def test_con_los_cinco_documentos_el_recorrido_llega_hasta_el_formulario(client):
    case_id = await _recorrer(client, clave="clave-buena")

    detalle = await client.get(f"/v1/cases/{case_id}")
    assert detalle.status_code == 200
    assert len(detalle.json()["documents"]) == 5

    # No se afirma el contenido de cada parada —eso lo cubren sus propias pruebas— sino que la
    # cadena no se corta y que ninguna se queda muda.
    for ruta in PARADAS:
        await _parada_responde_o_se_explica(client, case_id, ruta)


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Caso 2: el primerizo. Es el caso real, y el que se rompio.
# ═══════════════════════════════════════════════════════════════════════════════════════════


async def test_sin_declaraciones_previas_la_extraccion_entrega_los_otros_tres(client):
    """Un documento que la DIAN no tiene NO puede tumbar la extraccion entera.

    Ya paso una vez por otra causa —un `ConnectError` que se escapaba del manejo por documento—
    y el trabajo quedaba FAILED con los tres documentos que ya se habian bajado en la basura.
    """
    creado = await client.post("/v1/cases", json={"id_number": CEDULA, "tax_year": ANIO})
    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": CEDULA, "dian_password": "clave-sindecl", "tax_year": ANIO},
    )
    final = await wait_for_status(client, extraccion.json()["job_id"], "SUCCEEDED", "FAILED")

    assert final["status"] == "SUCCEEDED", "faltar dos documentos es un éxito parcial, no un fallo"
    assert {d["doc_type"] for d in final["documents"]} == {"RUT", "EXOGENA", "EINVOICE_SUMMARY"}
    assert {f["doc_type"] for f in final["failures"]} == {"PRIOR_RETURN", "SUGGESTED_RETURN"}
    assert creado.status_code == 201


async def test_la_falla_dice_por_que_falta_y_no_solo_que_falta(client):
    """La distincion que costo una tarde de depuracion: "no se pudo obtener" y "no existe" son
    cosas distintas, y el mensaje tiene que dejar claro cual de las dos es.

    Ademas cita a la DIAN. Sin esa cita, "no hay declaracion", "el endpoint cambio" y "la sesion
    no alcanza" llegan al expediente como la misma frase.
    """
    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": CEDULA, "dian_password": "clave-sindecl", "tax_year": ANIO},
    )
    final = await wait_for_status(client, extraccion.json()["job_id"], "SUCCEEDED", "FAILED")

    falla = next(f for f in final["failures"] if f["doc_type"] == "PRIOR_RETURN")
    assert falla["code"] == "DIAN_DOCUMENT_UNAVAILABLE"
    assert falla["retryable"] is False, "volver a pedirla no la va a hacer aparecer"
    assert "Documentos no encontrados" in falla["message"]
    assert "verificarlo en el portal" in falla["message"]


async def test_una_alerta_por_documento_aunque_se_reintente(client):
    """Se reintento la extraccion cuatro veces y el expediente acumulo siete alertas para dos
    documentos. Un contador que abre eso no ve dos problemas: ve siete."""
    creado = await client.post("/v1/cases", json={"id_number": CEDULA, "tax_year": ANIO})
    case_id = creado.json()["id"]

    for _ in range(3):
        extraccion = await client.post(
            "/v1/extractions",
            json={"id_number": CEDULA, "dian_password": "clave-sindecl", "tax_year": ANIO},
        )
        job_id = extraccion.json()["job_id"]
        await wait_for_status(client, job_id, "SUCCEEDED", "FAILED")
        await client.post(f"/v1/cases/{case_id}/link-extraction", json={"job_id": job_id})

    detalle = (await client.get(f"/v1/cases/{case_id}")).json()
    vivas = [f for f in detalle["flags"] if f["resolved_at"] is None]
    de_la_dian = [f for f in vivas if f["code"] == "DIAN_DOCUMENT_UNAVAILABLE"]
    assert len(de_la_dian) == 2, f"una por documento, no una por intento: {de_la_dian}"


async def test_el_recorrido_sigue_hasta_el_formulario_sin_las_declaraciones(client):
    """Las declaraciones son insumo de la comparacion, no del calculo.

    Si su ausencia tumbara el formulario, el producto no serviria para un primerizo — que es
    justo el cliente que mas lo necesita.
    """
    case_id = await _recorrer(client, clave="clave-sindecl")

    for ruta in PARADAS:
        await _parada_responde_o_se_explica(client, case_id, ruta)


@pytest.mark.parametrize("ruta", COMPARACIONES)
async def test_la_comparacion_que_no_se_puede_hacer_se_niega_explicando(client, ruta):
    """LA PANTALLA VACIA ES EL DEFECTO, y el backend no es el culpable.

    Es lo que se veia en produccion: la pestana "Comparar" pintaba sus dos encabezados y nada
    mas. La causa que parecia obvia —faltan los dos documentos de la DIAN— resulto no ser la
    verdadera: el expediente nunca habia pasado por la conciliacion, asi que no hay liquidacion,
    y sin liquidacion no hay con que comparar aunque los documentos estuvieran.

    El backend hace lo correcto: se niega con un codigo estable y una frase que lo dice. Esta
    prueba fija ese contrato para que el front pueda apoyarse en el — porque quien esta callando
    la explicacion es el front, no la API.
    """
    case_id = await _recorrer(client, clave="clave-sindecl")

    respuesta = await client.get(f"/v1/cases/{case_id}/{ruta}")
    assert respuesta.status_code == 409
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "LIQUIDACION_NO_DISPONIBLE"
    assert len(cuerpo["message"]) > 20, "negarse sin explicar deja la pantalla muda"
