"""Deshacer una respuesta: la pregunta vuelve a estar SIN CONTESTAR.

═══ POR QUE NO ALCANZABA CON CONTESTAR AL REVES ═══

Contestar por error pasa, y más de lo que parece: alguien recorre el cuestionario probando y
deja marcado "no tiene dependientes" en un expediente real. Hasta acá la única salida era
contestar lo contrario, y eso NO devuelve las cosas a como estaban.

"Sin contestar" y "contestó que no" son estados distintos y el sistema los trata distinto: el
primero deja la pregunta viva en la cola, el segundo la apaga para siempre. Escribir un "sí"
encima de un "no" deja un tercer estado que tampoco es el original, y encima afirma en nombre
del cliente algo que el cliente nunca dijo.

═══ Y DESHACER NO BORRA EL RASTRO ═══

La respuesta se va, el registro de que existió se queda. La bitácora es lo que respalda la
garantía si la DIAN pregunta: un expediente donde las decisiones desaparecen sin huella no
respalda nada, y hace imposible entender por qué una declaración quedó como quedó.
"""

from tests.integration.test_conciliacion_api import FILA_SALARIO, _conciliado

BASE = "/v1/cases"
PREGUNTA = "DEPENDIENTES"


async def _peticiones(client, case_id: str) -> list[str]:
    return [p["id"] for p in (await client.get(f"{BASE}/{case_id}/peticiones")).json()]


async def test_la_pregunta_vuelve_a_la_cola(client):
    case_id = await _conciliado(client, FILA_SALARIO)
    assert PREGUNTA in await _peticiones(client, case_id)

    # Se contesta que no: la pregunta se apaga.
    await client.post(
        f"{BASE}/{case_id}/respuestas",
        json={"pregunta": PREGUNTA, "tiene": False, "quien": "cliente", "detalle": {}},
    )
    assert PREGUNTA not in await _peticiones(client, case_id)

    # Se deshace: vuelve, porque "sin contestar" es el estado original y no un "sí".
    deshecha = await client.request("DELETE", f"{BASE}/{case_id}/respuestas/{PREGUNTA}")
    assert deshecha.status_code == 200, deshecha.text
    assert deshecha.json()["tiene"] is None, "sin contestar no es ni si ni no"
    assert PREGUNTA in await _peticiones(client, case_id)


async def test_queda_el_rastro_de_la_vuelta_completa(client):
    """Se contestó y se deshizo: las dos cosas pasaron y las dos quedan."""
    case_id = await _conciliado(client, FILA_SALARIO)
    await client.post(
        f"{BASE}/{case_id}/respuestas",
        json={"pregunta": PREGUNTA, "tiene": False, "quien": "cliente", "detalle": {}},
    )
    await client.request("DELETE", f"{BASE}/{case_id}/respuestas/{PREGUNTA}")

    eventos = (await client.get(f"{BASE}/{case_id}")).json()["events"]
    tipos = [e["kind"] for e in eventos]
    assert "ANSWER_RECORDED" in tipos, "la respuesta original no se borra de la bitacora"
    assert "ANSWER_UNDONE" in tipos, "y deshacerla tambien es un hecho que hay que registrar"

    deshecho = next(e for e in eventos if e["kind"] == "ANSWER_UNDONE")
    assert "sin contestar" in deshecho["message"]


async def test_deshacer_lo_que_nunca_se_contesto_no_falla(client):
    """Es idempotente: el resultado que se pide —que no haya respuesta— ya se cumple, así que
    negarse con un error obligaría a quien llama a distinguir dos casos que le dan igual."""
    case_id = await _conciliado(client, FILA_SALARIO)

    respuesta = await client.request("DELETE", f"{BASE}/{case_id}/respuestas/{PREGUNTA}")
    assert respuesta.status_code == 200, respuesta.text

    # Y no inventa un evento de algo que no pasó.
    eventos = (await client.get(f"{BASE}/{case_id}")).json()["events"]
    assert not [e for e in eventos if e["kind"] == "ANSWER_UNDONE"]
