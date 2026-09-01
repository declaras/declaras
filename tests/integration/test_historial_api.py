"""El historial de declaraciones: que años declaro el contribuyente y que años no.

POR QUE ESTAS PRUEBAS EXISTEN: el expediente traia la declaracion del año anterior (la
necesita el motor como insumo) y nada mas, asi que la vida tributaria de la persona no se
podia ver desde ningun lado. El primer caso real donde se noto fue uno donde la DIAN SI tenia
declaraciones de 2023, 2022, 2021 y 2020, y el expediente solo sabia decir que la de 2024 no
estaba.

Lo que se fija aca:
  - el año que FALTA en la mitad de la serie se marca como tal (es un atraso, y se puede vender)
  - "no se sabe" NO es lo mismo que "no declaro"
  - la declaracion del año anterior, que llega por la extraccion con otro nombre, aparece en el
    historial igual: para quien mira, es la declaracion de ese año y punto
  - todo llega con la consulta a la DIAN: no hay un segundo paso ni una clave que volver a
    escribir
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


async def test_el_hueco_se_marca_sin_volver_a_preguntar(client):
    """EL AÑO QUE FALTA ES EL DATO, y se deduce sin gastar una consulta mas.

    La consulta trae las declaraciones MAS RECIENTES que la DIAN tenga. Entonces, si hay una de
    2022 pero no de 2023, no es que falte por traer: es que la DIAN no la tiene, porque 2023 se
    habria traido antes que 2022. El fake deja 2024 por fuera a proposito, igual que el
    expediente real donde se noto que el historial no se veia.

    Mas atras del año mas viejo que tenemos ya no se puede afirmar nada, y esos años quedan
    "sin revisar" en vez de acusar a alguien de no haber declarado.
    """
    from tests.integration.test_cases_api import wait_for_status

    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = creado.json()["id"]
    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-buena", "tax_year": 2025},
    )
    await wait_for_status(client, extraccion.json()["job_id"], "SUCCEEDED")
    await client.post(
        f"{BASE}/{case_id}/link-extraction", json={"job_id": extraccion.json()["job_id"]}
    )

    por_anio = {f["anio"]: f for f in (await client.get(f"{BASE}/{case_id}/historial")).json()}

    # 2023 esta ENTRE las que si tenemos (2024 y 2022), asi que su ausencia es informacion.
    assert por_anio[2023]["estado"] == "sin_declaracion"
    # 2020 esta mas atras de lo que trae la consulta: ahi no se sabe, y se dice.
    assert por_anio[2020]["estado"] == "sin_revisar"


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

    # El año anterior llega como PRIOR_RETURN y los dos de atras como historial.
    for anio in (2024, 2022, 2021):
        assert por_anio[anio]["estado"] == "guardada", f"{anio} tenia que venir con la consulta"
        assert por_anio[anio]["document_id"]

    # Y el mas viejo sigue sin revisar: no se afirma que no declaro sin poder saberlo.
    assert por_anio[2020]["estado"] == "sin_revisar"
