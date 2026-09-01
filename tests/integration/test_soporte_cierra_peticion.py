"""Subir el soporte de una petición la cierra, y lo que falta se dice.

═══ EL LAZO QUE ESTABA ABIERTO ═══

Lo único que apagaba una petición era que el beneficio estuviera EN EL CÁLCULO, o sea que su
documento se hubiera podido LEER. Y hay documentos que no se leen ni deberían: un registro
civil no es un certificado con cifras, es una prueba de parentesco.

El resultado era el peor posible: el sistema pedía un papel cuya llegada no podía detectar. El
cliente lo mandaba, quedaba guardado, y la petición seguía viva pidiendo lo mismo para siempre.
Fue exactamente lo que se vio en un expediente real: "¿por qué sale esto si el certificado ya
está cargado?".

═══ Y LA CONTRAPARTE, QUE ES LA MITAD QUE IMPORTA ═══

Dejar de pedirlo no puede significar darlo por aplicado. Si la petición se cerrara en silencio,
el cliente habría mandado su papel para PERDER el beneficio sin que nadie se entere: se cambia
una molestia (pedir dos veces) por un daño (plata que no se ahorra). Por eso al subir un
soporte que nadie puede leer queda un aviso diciendo qué hay que capturar.
"""

BASE = "/v1/cases"


async def _caso_con_peticion_de_dependientes(client) -> tuple[str, dict]:
    """Un expediente donde el sistema pide el soporte de personas a cargo."""
    from tests.integration.test_conciliacion_api import FILA_SALARIO, _conciliado

    case_id = await _conciliado(client, FILA_SALARIO)
    peticiones = (await client.get(f"{BASE}/{case_id}/peticiones")).json()
    dependientes = next((p for p in peticiones if p["id"] == "DEPENDIENTES"), None)
    assert dependientes, f"el caso tenia que pedir el soporte de dependientes: {peticiones}"
    return case_id, dependientes


async def test_el_soporte_subido_deja_de_pedirse(client):
    case_id, peticion = await _caso_con_peticion_de_dependientes(client)

    subida = await client.post(
        f"{BASE}/{case_id}/documents",
        data={"doc_type": peticion["tipo_documento"]},
        files={"file": ("registro-civil.pdf", b"%PDF-1.4 registro", "application/pdf")},
    )
    assert subida.status_code == 200, subida.text

    quedan = (await client.get(f"{BASE}/{case_id}/peticiones")).json()
    assert not any(p["id"] == "DEPENDIENTES" for p in quedan), (
        "el soporte ya esta en el expediente: pedirlo otra vez es pedirle al cliente algo que "
        f"ya mando. Quedaron: {[p['id'] for p in quedan]}"
    )


async def test_y_queda_dicho_que_falta_capturar_el_dato(client):
    """La contraparte: cerrar la peticion sin aplicar el beneficio y sin decirlo seria
    cambiar una molestia por un daño."""
    case_id, peticion = await _caso_con_peticion_de_dependientes(client)

    await client.post(
        f"{BASE}/{case_id}/documents",
        data={"doc_type": peticion["tipo_documento"]},
        files={"file": ("registro-civil.pdf", b"%PDF-1.4 registro", "application/pdf")},
    )

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    avisos = [f for f in detalle["flags"] if f["code"] == "SOPORTE_SIN_DATO"]
    assert avisos, f"tenia que quedar el aviso de captura: {detalle['flags']}"
    # Dice QUE capturar, no solo que algo falta: un aviso que no nombra la accion no se atiende.
    assert "personas a cargo" in avisos[0]["message"]
    assert avisos[0]["severity"] == "warning", "no bloquea el expediente, pide atencion"


async def test_un_documento_que_no_soporta_ningun_beneficio_no_levanta_aviso(client):
    """El aviso es para los soportes de beneficio. Cualquier otro documento sin lector no tiene
    nada que capturar, y un aviso que no pide nada entrena a ignorarlos todos."""
    from tests.integration.test_conciliacion_api import FILA_SALARIO, _conciliado

    case_id = await _conciliado(client, FILA_SALARIO)
    await client.post(
        f"{BASE}/{case_id}/documents",
        data={"doc_type": "OTRO_PAPEL"},
        files={"file": ("algo.pdf", b"%PDF-1.4 algo", "application/pdf")},
    )

    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    assert not [f for f in detalle["flags"] if f["code"] == "SOPORTE_SIN_DATO"]
