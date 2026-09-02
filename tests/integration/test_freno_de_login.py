"""El freno anti bloqueo, en TODOS los caminos que abren sesion en el portal.

═══ EL ATAQUE QUE ESTAS PRUEBAS CIERRAN ═══

La DIAN bloquea la cuenta al TERCER intento fallido, y desbloquearla es un tramite, no un
boton. La consulta de "¿me toca declarar?" es publica —no exige autenticacion, porque su razon
de ser es contestarle a un desconocido— y abre sesion en el portal con la cedula y la clave que
le manden.

Sin contador de intentos, eso alcanza para hacerle dano a un tercero: se toma la cedula de una
persona (en Colombia esta en cualquier factura), se mandan tres peticiones con claves
inventadas, y esa persona queda sin poder declarar. Un curl en bucle, sin ser cliente de nadie.

El freno ya existia y solo lo usaba el flujo de extraccion, porque vivia dentro de ese servicio
en vez de vivir junto a la operacion que hay que frenar. Estas pruebas fijan que los cuatro
caminos pasen por el, y estan escritas por CAMINO —no una sola generica— porque lo que hay que
impedir es justamente que el proximo camino nuevo se olvide.
"""

from tests.integration.test_escritura_api import _listo_para_escribir

# El fake falla el login cuando la clave contiene "bad", y el limite son 2 intentos: se corta
# uno antes del tercero, que es el que bloquea de verdad.
MALA = "clave-bad"
BUENA = "clave-buena"
CEDULA = "141070249"


async def _consulta_dian(client, clave: str, cedula: str = CEDULA):
    return await client.post(
        "/v1/consultas/dian",
        json={
            "nombre": "Quien Sea",
            "correo": "quien@sea.co",
            "whatsapp": "3001234567",
            "id_number": cedula,
            "dian_password": clave,
            "tax_year": 2025,
        },
    )


async def test_la_consulta_publica_no_puede_quemar_la_cuenta_de_un_tercero(client):
    """El caso grave: endpoint publico + cedula ajena + claves inventadas.

    Al tercer intento la respuesta ya NO es "clave incorrecta" sino que no quedan intentos, y
    lo que importa es que ese tercer intento no llegue al portal: es el que bloquea la cuenta.
    """
    primero = await _consulta_dian(client, MALA)
    assert primero.status_code == 401, primero.text
    assert primero.json()["code"] == "DIAN_INVALID_CREDENTIALS"

    segundo = await _consulta_dian(client, MALA)
    assert segundo.status_code == 401

    tercero = await _consulta_dian(client, MALA)
    assert tercero.json()["code"] == "DIAN_LOGIN_ATTEMPTS_EXHAUSTED", tercero.text


async def test_el_freno_avisa_cuantos_intentos_quedan(client):
    """Quien escribio mal su clave tiene que saber que le queda UNO antes del bloqueo: sin ese
    dato, el siguiente intento a ciegas es el que le cuesta la cuenta."""
    respuesta = await _consulta_dian(client, MALA)
    assert respuesta.json()["details"]["attempts_remaining"] == 1


async def test_un_ingreso_bueno_limpia_los_fallos(client):
    """La DIAN cuenta fallos CONSECUTIVOS. Si no se limpiaran, dos claves mal escritas a lo
    largo de un mes dejarian al contribuyente a un intento del bloqueo para siempre."""
    assert (await _consulta_dian(client, MALA)).status_code == 401
    assert (await _consulta_dian(client, BUENA)).status_code == 200

    # Y despues del exito vuelve a haber dos intentos, no cero.
    assert (await _consulta_dian(client, MALA)).json()["details"]["attempts_remaining"] == 1


async def test_el_freno_es_por_cedula_y_no_global(client):
    """Se protege al TITULAR, asi que quemar los intentos de una cedula no puede dejar sin
    servicio a las demas: si no, bastaria una cedula cualquiera para tumbar el producto."""
    await _consulta_dian(client, MALA)
    await _consulta_dian(client, MALA)
    agotada = await _consulta_dian(client, MALA)
    assert agotada.json()["code"] == "DIAN_LOGIN_ATTEMPTS_EXHAUSTED"

    otra = await _consulta_dian(client, BUENA, cedula="987654321")
    assert otra.status_code == 200, otra.text


async def test_escribir_el_borrador_tambien_frena(client):
    """Escribir el borrador tambien empieza por un login, y un login fallido cuenta para el
    bloqueo igual que el de la extraccion.

    Necesita el borrador cerrado: su compuerta de producto (un 409) corta ANTES de llegar al
    portal, asi que sin cerrarlo esta prueba pasaria por la razon equivocada y no probaria el
    freno.
    """
    case_id = await _listo_para_escribir(client)
    ruta = f"/v1/cases/{case_id}/portal/escribir"

    primera = await client.post(ruta, json={"dian_password": MALA})
    assert primera.json()["code"] == "DIAN_INVALID_CREDENTIALS", primera.text
    await client.post(ruta, json={"dian_password": MALA})

    agotada = await client.post(ruta, json={"dian_password": MALA})
    assert agotada.json()["code"] == "DIAN_LOGIN_ATTEMPTS_EXHAUSTED", agotada.text


async def test_el_boton_de_desbloquear_libera_los_intentos_del_expediente(client):
    """La salida cuando alguien tecleó mal la clave hasta agotar NUESTRO freno.

    Sin este reinicio, quien se equivocó dos veces tendría que esperar la ventana entera para
    escribir bien la clave, aunque ya sepa cuál es. El botón lo deja intentar de una, y la
    cuenta de la DIAN sigue protegida: la DIAN cuenta sus propios fallos, no los nuestros.
    """
    case_id = await _listo_para_escribir(client)
    ruta = f"/v1/cases/{case_id}/portal/escribir"

    # Se agotan los intentos con clave mala.
    await client.post(ruta, json={"dian_password": MALA})
    await client.post(ruta, json={"dian_password": MALA})
    agotada = await client.post(ruta, json={"dian_password": MALA})
    assert agotada.json()["code"] == "DIAN_LOGIN_ATTEMPTS_EXHAUSTED"

    # El botón reinicia el contador.
    desbloqueo = await client.post(f"/v1/cases/{case_id}/desbloquear-intentos")
    assert desbloqueo.status_code == 200, desbloqueo.text

    # Y ahora la clave buena vuelve a tener oportunidad (ya no es EXHAUSTED).
    reintento = await client.post(ruta, json={"dian_password": BUENA})
    assert reintento.status_code == 200, reintento.text
