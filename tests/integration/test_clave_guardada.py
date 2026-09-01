"""La clave del portal, guardada para no pedirla en cada paso.

═══ POR QUE CAMBIO LA REGLA ═══

Preparar una declaración son varias visitas al portal repartidas en días: consultar, volver a
consultar cuando la DIAN publica la exógena, escribir el borrador. Con la clave efímera cada
una la pedía de nuevo, y quien opera la consola NO la tiene: hay que llamar al cliente. En la
práctica, una llamada por paso.

═══ LO QUE ESTAS PRUEBAS PROTEGEN ═══

Guardar una credencial ajena solo es legítimo con tres cosas, y las tres se fijan acá:

  1. Nunca en claro. Si el despliegue no puede cifrar, NO guarda: la alternativa sería dejar la
     clave de un contribuyente legible para cualquiera que lea la base.
  2. Se puede retirar. Una clave guardada sin forma de borrarla no es una función, es una
     trampa.
  3. Solo se guarda la que sirvió. Archivar una clave que falló haría que el siguiente paso la
     use sola y falle sin que nadie entienda por qué.
"""

from tests.integration.test_escritura_api import _listo_para_escribir

BASE = "/v1/cases"


async def test_despues_de_escribir_no_hay_que_volver_a_ponerla(client):
    case_id = await _listo_para_escribir(client)

    primera = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"}
    )
    assert primera.status_code == 200, primera.text

    # La segunda va SIN clave: la guardada alcanza.
    segunda = await client.post(f"{BASE}/{case_id}/portal/escribir", json={})
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["verificado"] is True


async def test_sin_clave_guardada_se_pide(client):
    """El error dice qué falta, no un 500 genérico."""
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(f"{BASE}/{case_id}/portal/escribir", json={})
    assert respuesta.status_code == 400, respuesta.text
    assert respuesta.json()["code"] == "SIN_CLAVE_DIAN"


async def test_la_clave_que_fallo_no_se_guarda(client):
    """Archivarla haría que el siguiente paso la use sola y falle sin explicación."""
    case_id = await _listo_para_escribir(client)

    fallida = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-bad"}
    )
    assert fallida.status_code != 200

    estado = await client.get(f"{BASE}/{case_id}/clave")
    assert estado.json()["guardada"] is False


async def test_se_puede_olvidar(client):
    """Que exista el botón es lo que hace legítimo el guardado."""
    case_id = await _listo_para_escribir(client)
    await client.post(f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"})
    assert (await client.get(f"{BASE}/{case_id}/clave")).json()["guardada"] is True

    await client.request("DELETE", f"{BASE}/{case_id}/clave")
    assert (await client.get(f"{BASE}/{case_id}/clave")).json()["guardada"] is False

    # Y despues vuelve a pedirse, que es el estado original.
    respuesta = await client.post(f"{BASE}/{case_id}/portal/escribir", json={})
    assert respuesta.json()["code"] == "SIN_CLAVE_DIAN"


async def test_nunca_queda_en_claro_en_la_base(client, container):
    """La prueba que justifica todo lo demás: quien se lleve un dump no puede leer ninguna."""
    from sqlalchemy import text

    case_id = await _listo_para_escribir(client)
    await client.post(f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"})

    async with container.engine.connect() as conn:
        filas = (await conn.execute(text("SELECT dian_password_cifrada FROM clients"))).all()
    guardadas = [f[0] for f in filas if f[0]]
    assert guardadas, "tenia que quedar guardada"
    assert all("clave-buena" not in g for g in guardadas)


async def test_la_consulta_exitosa_tambien_guarda_la_clave(client):
    """LA EXTRACCION ES LO PRIMERO QUE SE HACE CON UN CLIENTE, así que era la clave que más
    veces se pedía: cada consulta la pedía de nuevo aunque la anterior hubiera funcionado,
    porque el único camino que guardaba era la escritura, el último paso del proceso.

    Se vio en el uso real: "no debería pedir contraseña, porque la pide aún".
    """
    from tests.integration.test_cases_api import wait_for_status

    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = creado.json()["id"]

    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-buena", "tax_year": 2025},
    )
    await wait_for_status(client, extraccion.json()["job_id"], "SUCCEEDED")

    estado = await client.get(f"{BASE}/{case_id}/clave")
    assert estado.json()["guardada"] is True, (
        "la clave que abrió la consulta funcionó: pedirla otra vez en el siguiente paso es "
        "pedir lo que ya se tiene"
    )


async def test_la_consulta_fallida_no_guarda_nada(client):
    """La misma regla de siempre: solo se archiva la clave que FUNCIONÓ."""
    from tests.integration.test_cases_api import wait_for_status

    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = creado.json()["id"]

    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-bad", "tax_year": 2025},
    )
    await wait_for_status(client, extraccion.json()["job_id"], "FAILED")

    estado = await client.get(f"{BASE}/{case_id}/clave")
    assert estado.json()["guardada"] is False
