"""Cuando el contribuyente firma, Clara se entera y lo muestra.

═══ EL CICLO NO SE CERRABA ═══

El expediente terminaba en "falta que entre a firmar" y ahí se quedaba PARA SIEMPRE, aunque el
contribuyente ya hubiera firmado: nada le avisaba al sistema. Quien lo abría un mes después no
tenía forma de saber si esa declaración se presentó o quedó en el limbo, y menos de ver qué fue
lo que quedó radicado.

La DIAN sí lo sabe: en cuanto se firma, aparece la declaración PRESENTADA de ese año. Bastaba
con pedirla en cada consulta —antes ni se pedía— y usarla como lo que es: la prueba de que el
trabajo terminó.
"""

BASE = "/v1/cases"


async def _consultar(client, case_id: str, clave: str) -> dict:
    from tests.integration.test_cases_api import wait_for_status

    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": clave, "tax_year": 2025},
    )
    job_id = extraccion.json()["job_id"]
    await wait_for_status(client, job_id, "SUCCEEDED")
    vinculada = await client.post(f"{BASE}/{case_id}/link-extraction", json={"job_id": job_id})
    assert vinculada.status_code == 200, vinculada.text
    return vinculada.json()


async def _abrir(client) -> str:
    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    return creado.json()["id"]


async def test_sin_presentar_el_expediente_sigue_en_revision(client):
    """El caso normal: se trabaja ANTES de firmar, así que la DIAN todavía no tiene nada."""
    case_id = await _abrir(client)
    detalle = await _consultar(client, case_id, "clave-buena")

    assert detalle["status"] == "READY_FOR_REVIEW"
    assert not [d for d in detalle["documents"] if d["doc_type"] == "FILED_RETURN"]


async def test_cuando_ya_se_firmo_el_expediente_queda_presentado(client):
    """El cierre del ciclo: la declaración presentada aparece y el expediente lo refleja."""
    case_id = await _abrir(client)
    detalle = await _consultar(client, case_id, "clave-presentada")

    assert detalle["status"] == "SUBMITTED", "el trabajo de este expediente terminó"
    presentadas = [d for d in detalle["documents"] if d["doc_type"] == "FILED_RETURN"]
    assert presentadas, "y queda el documento para poder verla"
    assert presentadas[0]["download_url"]

    # Queda constancia: sin el evento, la bitácora no explicaría por qué cambió de estado.
    assert any(e["kind"] == "FILED" for e in detalle["events"])


async def test_el_historial_de_años_viejos_no_marca_el_año_como_presentado(client):
    """La distinción que evita mentir: el historial baja declaraciones presentadas de años
    ANTERIORES con este mismo tipo. Sin mirar el año, traer el historial de alguien marcaría su
    declaración de ESTE año como presentada, que es al revés de la verdad."""
    case_id = await _abrir(client)
    detalle = await _consultar(client, case_id, "clave-buena")

    # El historial sí trajo declaraciones (de años anteriores).
    assert [d for d in detalle["documents"] if d["doc_type"].startswith("DECLARACION_")]
    # Y aun así el expediente NO está presentado.
    assert detalle["status"] == "READY_FOR_REVIEW"


async def test_la_comparacion_con_lo_presentado_ya_tiene_de_donde_salir(client):
    """La cadena que estaba cortada, de punta a punta.

    El endpoint `comparacion-con-lo-presentado` existia y funcionaba, pero no podia devolver nada
    util NUNCA: comparaba contra un documento que el sistema no le pedia a la DIAN. Esta prueba
    fija el eslabon que faltaba —que la presentada llegue al expediente— y por eso mira el
    documento, no el resultado de la comparacion: con los PDF de prueba, que no son legibles,
    la comparacion sale "no disponible" por una razon distinta a la que se esta arreglando.
    """
    case_id = await _abrir(client)
    await _consultar(client, case_id, "clave-presentada")

    # El endpoint contesta, o explica por que no puede: sin conciliar no hay formulario nuestro
    # que poner del otro lado, y negarse con un codigo estable es la respuesta correcta ahi.
    respuesta = await client.get(f"{BASE}/{case_id}/comparacion-con-lo-presentado")
    assert respuesta.status_code in (200, 409), respuesta.text
    if respuesta.status_code == 200:
        assert respuesta.json()["contra"] == "DECLARACION_PRESENTADA"
    else:
        assert respuesta.json()["code"], "una negativa sin codigo deja a la pantalla muda"

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    assert [d for d in detalle["documents"] if d["doc_type"] == "FILED_RETURN"], (
        "sin el documento en el expediente la comparacion no tiene contra que comparar"
    )


async def test_no_haber_presentado_todavia_no_ensucia_el_expediente_con_alertas(client):
    """Una alerta que sale SIEMPRE no informa; ensena a ignorar las alertas.

    Al empezar a pedir la declaracion presentada, su ausencia entraba como alerta BLOQUEANTE en
    todos los expedientes: la ausencia es el estado normal de quien nos contrato justamente para
    presentar. La falla si queda registrada en la extraccion, que es la traza de lo que se
    intento; lo que no hace es pedir atencion sobre lo esperado.
    """
    case_id = await _abrir(client)
    detalle = await _consultar(client, case_id, "clave-buena")

    alertas = [f for f in detalle["flags"] if "presentada" in f["message"].lower()]
    assert not alertas, f"la ausencia esperada no genera alerta: {[a['message'] for a in alertas]}"


async def test_reconsultar_no_devuelve_a_revision_un_expediente_ya_presentado(client):
    """Una declaracion firmada no se des-presenta.

    El portal de la DIAN se cae seguido. Si el estado dependiera de si la descarga funciono HOY,
    una reconsulta en un mal dia devolveria un expediente terminado a "por revisar" y quien lo
    abriera lo daria por pendiente. Lo unico que sigue despues de presentar es una correccion,
    que tambien es una declaracion presentada.
    """
    case_id = await _abrir(client)
    presentado = await _consultar(client, case_id, "clave-presentada")
    assert presentado["status"] == "SUBMITTED"

    # Segunda consulta, esta vez sin que la DIAN entregue la presentada.
    detalle = await _consultar(client, case_id, "clave-buena")

    assert detalle["status"] == "SUBMITTED", "sigue presentada: no depende de la consulta de hoy"
    # Y el evento no se repite en cada consulta: se presento una vez.
    assert len([e for e in detalle["events"] if e["kind"] == "FILED"]) == 1
