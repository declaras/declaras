"""El limite por punto de acceso, en la unica ruta publica del API.

═══ QUE PROTEGE, QUE NO ES LO MISMO QUE EL FRENO DE LOGIN ═══

El freno de login cuenta por CEDULA y protege la cuenta del contribuyente. Esto cuenta por
ORIGEN y protege la IP de este servicio: la consulta publica hace que nuestro servidor entre al
portal de la DIAN, asi que alguien que la llame en bucle con cedulas distintas —cada una con
sus dos intentos disponibles, o sea sin violar el otro freno— nos convierte en el que golpea el
portal miles de veces. El que termina bloqueado por la DIAN es nuestro despliegue, con todos
los clientes adentro.

La prueba que mas importa aca es la del header falsificado, porque es el error que vuelve
inservible al limitador sin que nada falle.
"""

BASE = "/v1/consultas"


def _consulta(n: int) -> dict:
    return {
        "nombre": f"Persona {n}",
        "correo": f"p{n}@ejemplo.co",
        "whatsapp": "3001234567",
        "via": "preguntas",
        "respuestas": {"ingresos": "no"},
    }


async def test_pasada_la_cuota_responde_429(client):
    """Y con 429, no 403: no es que al origen le falte permiso, es que pidio demasiado
    seguido, y la diferencia es que esto SI se puede reintentar mas tarde."""
    for n in range(40):
        respuesta = await client.post(BASE, json=_consulta(n))
        assert respuesta.status_code == 200, f"la {n} tenia que pasar: {respuesta.text}"

    excedida = await client.post(BASE, json=_consulta(41))
    assert excedida.status_code == 429, excedida.text
    cuerpo = excedida.json()
    assert cuerpo["code"] == "DEMASIADAS_PETICIONES"
    # Reintentar sirve pasada la ventana, y eso se le dice a quien llama.
    assert cuerpo["retryable"] is True


async def test_un_header_falsificado_no_multiplica_los_origenes(client):
    """LA TRAMPA DEL X-FORWARDED-FOR, que es el error que deja el limitador de adorno.

    Un proxy AGREGA su valor al final del header, no lo reemplaza. Asi que si se toma el primer
    valor —que es lo que parece correcto leyendo la definicion del header— basta mandar uno
    distinto en cada peticion para tener origenes infinitos y cuota infinita.

    Aca todas las peticiones mandan un primer valor distinto y aun asi comparten cuota, porque
    lo que cuenta es el ultimo, el unico que escribio alguien en quien confiamos.
    """
    for n in range(40):
        respuesta = await client.post(
            BASE,
            json=_consulta(n),
            headers={"X-Forwarded-For": f"10.0.0.{n}, 200.1.1.1"},
        )
        assert respuesta.status_code == 200, respuesta.text

    excedida = await client.post(
        BASE,
        json=_consulta(99),
        headers={"X-Forwarded-For": "10.0.0.99, 200.1.1.1"},
    )
    assert excedida.status_code == 429, "el primer valor del header no puede dar cuota nueva"


async def test_origenes_distintos_tienen_cuota_propia(client):
    """Un origen que gasta su cuota no puede dejar sin servicio a los demas: si no, bastaria un
    script para tumbar el producto para todo el mundo."""
    for n in range(41):
        await client.post(
            BASE, json=_consulta(n), headers={"X-Forwarded-For": "200.1.1.1"}
        )

    otro = await client.post(
        BASE, json=_consulta(1), headers={"X-Forwarded-For": "200.2.2.2"}
    )
    assert otro.status_code == 200, otro.text


async def test_cada_recurso_lleva_su_propia_cuenta(client):
    """Registrar una consulta y consultar el portal cuestan muy distinto, asi que gastar la
    cuota de lo barato no puede cerrar lo caro (ni al reves)."""
    for n in range(41):
        await client.post(
            BASE, json=_consulta(n), headers={"X-Forwarded-For": "200.3.3.3"}
        )

    # La cuota de /consultas quedo agotada; la de /consultas/dian no se toco. Se manda con una
    # clave que el conector falso rechaza: lo que se comprueba es que NO responde 429.
    portal = await client.post(
        f"{BASE}/dian",
        json={
            "nombre": "Persona",
            "correo": "p@ejemplo.co",
            "whatsapp": "3001234567",
            "id_number": "1020304050",
            "dian_password": "clave-bad",
            "tax_year": 2025,
        },
        headers={"X-Forwarded-For": "200.3.3.3"},
    )
    assert portal.status_code != 429, portal.text


async def test_las_ventanas_viejas_se_barren(client, container):
    """Una fila por origen y por hora que nada vuelve a leer despues de su hora: sin barrerlas
    la tabla crece para siempre, y una tabla que solo crece termina siendo un problema de
    operacion que nadie vio venir. El worker las barre en cada ronda de limpieza."""
    from datetime import UTC, datetime, timedelta

    await client.post(BASE, json=_consulta(1), headers={"X-Forwarded-For": "200.9.9.9"})

    # Un corte en el futuro deja la ventana de esta hora del lado de lo viejo, que es la unica
    # forma de comprobar el barrido sin esperar una hora.
    borradas = await container.limitador.limpiar(antes_de=datetime.now(UTC) + timedelta(hours=1))
    assert borradas >= 1

    # Y despues del barrido la cuota vuelve a estar entera: la fila era el contador.
    for n in range(40):
        respuesta = await client.post(
            BASE, json=_consulta(n), headers={"X-Forwarded-For": "200.9.9.9"}
        )
        assert respuesta.status_code == 200, respuesta.text
