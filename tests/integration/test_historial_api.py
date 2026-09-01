"""El historial de declaraciones: que años declaro el contribuyente y que años no.

POR QUE ESTAS PRUEBAS EXISTEN: el expediente traia la declaracion del año anterior (la
necesita el motor como insumo) y nada mas, asi que la vida tributaria de la persona no se
podia ver desde ningun lado. El primer caso real donde se noto fue uno donde la DIAN SI tenia
declaraciones de 2023, 2022, 2021 y 2020, y el expediente solo sabia decir que la de 2024 no
estaba.

Lo que se fija aca:
  - el año que FALTA en la mitad de la serie se marca como tal (es un atraso, y se puede vender)
  - "no se ha preguntado" NO es lo mismo que "no declaro"
  - la declaracion del año anterior, que llega por la extraccion con otro nombre, aparece en el
    historial igual: para quien mira, es la declaracion de ese año y punto
  - abrir UNA sesion para todo el historial, porque abrirla es lo que la DIAN cuenta para
    bloquear la cuenta
"""

from tests.integration.test_conciliacion_api import FILA_SALARIO, _abrir_caso

BASE = "/v1/cases"


async def test_sin_preguntar_no_se_afirma_que_no_declaro(client):
    """La distincion que evita inventar: sin consultar el portal, un año sin documento es
    `sin_revisar`. Marcarlo como "no declaró" seria afirmar algo sobre la vida tributaria de
    una persona a partir de no haber mirado."""
    case_id = await _abrir_caso(client, FILA_SALARIO)

    respuesta = await client.get(f"{BASE}/{case_id}/historial")
    assert respuesta.status_code == 200, respuesta.text
    filas = respuesta.json()

    # Cinco años hacia atras desde el anterior al del expediente (2025).
    assert [f["anio"] for f in filas] == [2024, 2023, 2022, 2021, 2020]
    assert {f["estado"] for f in filas} == {"sin_revisar"}
    assert all(f["document_id"] is None for f in filas)


async def test_trae_las_que_hay_y_marca_el_hueco(client):
    """El caso completo: la DIAN tiene 2023, 2022, 2021 y 2020 pero NO 2024.

    Ese hueco es el dato que importa: es una declaracion que no se presento, o sea un atraso.
    """
    case_id = await _abrir_caso(client, FILA_SALARIO)

    respuesta = await client.post(
        f"{BASE}/{case_id}/historial", json={"dian_password": "clave-buena"}
    )
    assert respuesta.status_code == 200, respuesta.text
    por_anio = {f["anio"]: f for f in respuesta.json()}

    # El fake deja 2024 por fuera a proposito, igual que el expediente real donde se noto.
    assert por_anio[2024]["estado"] == "sin_declaracion"
    assert por_anio[2024]["document_id"] is None

    for anio in (2023, 2022, 2021, 2020):
        assert por_anio[anio]["estado"] == "guardada", anio
        assert por_anio[anio]["document_id"], f"{anio} tiene que quedar descargable"
        assert por_anio[anio]["filename"] == f"declaracion-{anio}.pdf"


async def test_quedan_en_los_documentos_del_expediente(client):
    """Cada año es su propio documento y NO se reemplazan entre si.

    El expediente reemplaza documentos del mismo tipo cuando llega uno nuevo. Si todas las
    declaraciones del historial se llamaran igual, cada descarga borraria la anterior y
    quedaria una sola: por eso el año va DENTRO del tipo.
    """
    case_id = await _abrir_caso(client, FILA_SALARIO)
    await client.post(f"{BASE}/{case_id}/historial", json={"dian_password": "clave-buena"})

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    tipos = {d["doc_type"] for d in detalle["documents"]}
    assert {"DECLARACION_2023", "DECLARACION_2022", "DECLARACION_2021", "DECLARACION_2020"} <= tipos

    # Y queda constancia en la bitacora, con el año que falta nombrado.
    eventos = [e for e in detalle["events"] if e["kind"] == "DIAN_QUERY"]
    assert any("2024" in e["message"] for e in eventos), eventos


async def test_volver_a_traer_no_duplica(client):
    """Repetir la consulta no vuelve a bajar lo que ya esta: es una peticion menos al portal,
    y el portal es justamente el recurso escaso."""
    case_id = await _abrir_caso(client, FILA_SALARIO)
    clave = {"dian_password": "clave-buena"}
    primera = await client.post(f"{BASE}/{case_id}/historial", json=clave)
    segunda = await client.post(f"{BASE}/{case_id}/historial", json=clave)
    assert segunda.status_code == 200, segunda.text
    assert primera.json() == segunda.json()

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    declaraciones = [d for d in detalle["documents"] if d["doc_type"].startswith("DECLARACION_")]
    assert len(declaraciones) == 4, [d["doc_type"] for d in declaraciones]


async def test_la_clave_no_queda_en_el_expediente(client):
    """La clave abre la sesion y se suelta. Vale escribirlo como prueba porque este endpoint
    la recibe en el cuerpo, que es justo donde es facil que se quede pegada en un log o en un
    payload de evento."""
    case_id = await _abrir_caso(client, FILA_SALARIO)
    await client.post(f"{BASE}/{case_id}/historial", json={"dian_password": "clave-secreta-123"})

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    assert "clave-secreta-123" not in str(detalle)


async def test_caso_que_no_existe(client):
    respuesta = await client.get(f"{BASE}/00000000-0000-0000-0000-000000000000/historial")
    assert respuesta.status_code == 404


async def test_la_consulta_a_la_dian_ya_trae_las_ultimas_dos(client):
    """El historial NO empieza vacio esperando que alguien toque un boton.

    Vivio un rato detras de "Revisar en la DIAN" y eso estaba mal por dos razones. La de
    producto: es un paso manual con clave otra vez, y este sistema existe para quitar pasos.
    La que decide: la sesion ya esta abierta en la extraccion, y lo escaso NO es la descarga
    sino el LOGIN —la DIAN bloquea la cuenta al tercer intento fallido—, asi que pedir la clave
    de nuevo gasta el recurso caro para ahorrarse el barato.

    Son DOS años y no cinco: el anterior es insumo del calculo y el de antes sirve para ver el
    patrimonio en serie. Los cinco siguen en el boton, que ahora es lo que su nombre dice.
    """
    from tests.integration.test_cases_api import wait_for_status

    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = creado.json()["id"]
    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-buena", "tax_year": 2025},
    )
    job_id = extraccion.json()["job_id"]
    await wait_for_status(client, job_id, "SUCCEEDED")
    vinculada = await client.post(f"{BASE}/{case_id}/link-extraction", json={"job_id": job_id})
    assert vinculada.status_code == 200, vinculada.text

    filas = (await client.get(f"{BASE}/{case_id}/historial")).json()
    por_anio = {f["anio"]: f for f in filas}

    for anio in (2023, 2022):
        assert por_anio[anio]["estado"] == "guardada", f"{anio} tenia que venir con la consulta"
        assert por_anio[anio]["document_id"]

    # Y los mas viejos siguen sin revisar: no se afirma que no declaro sin haber preguntado.
    assert por_anio[2020]["estado"] == "sin_revisar"
