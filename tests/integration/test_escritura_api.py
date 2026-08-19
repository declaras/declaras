"""Escribir el 210 en el portal: el ultimo tramo, con sus compuertas.

Hasta aqui el contador transcribia a mano las casillas calculadas. Estas pruebas fijan el
contrato del tramo que lo reemplaza: solo se escribe un borrador DADO POR LISTO, la clave
viaja en la peticion y no se persiste, la cedula sale del expediente (nunca de la peticion),
y el resultado carga la verificacion de relectura, que es lo que hace confiable un 201.
"""

from tests.integration.test_conciliacion_api import (
    FILA_SALARIO,
    _conciliado,
    _sin_patrimonio,
)

BASE = "/v1/cases"


async def _listo_para_escribir(client) -> str:
    """Un expediente con el borrador dado por listo, que es la compuerta de la escritura."""
    case_id = await _conciliado(client, FILA_SALARIO)
    await _sin_patrimonio(client, case_id)
    cerrado = await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")
    assert cerrado.status_code == 200, cerrado.text
    return case_id


async def test_escribe_y_verifica(client):
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"}
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["verificado"] is True
    assert cuerpo["escritas"] > 0
    assert cuerpo["anio"] == 2025
    # Los formularios del portal empiezan por 21; el fake imita la forma real.
    assert cuerpo["form_id"].startswith("21")

    # Queda constancia en la bitacora: escribir en la cuenta de alguien no puede no dejar rastro.
    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    eventos = [e for e in detalle["events"] if e["kind"] == "PORTAL_WRITE"]
    assert eventos, "la escritura tiene que quedar en la bitacora"
    assert "verificado" in eventos[0]["message"]


async def test_sin_cerrar_no_se_escribe(client):
    """La compuerta: un 210 a medio decidir en el Muisca es peor que ninguno, porque nada
    en el portal dice que esta incompleto."""
    case_id = await _conciliado(client, FILA_SALARIO)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"}
    )
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "BORRADOR_NO_CERRADO"


async def test_sin_borrador_en_el_portal_dice_como_crearlo(client):
    """La cuenta sin borrador del año no es un error criptico: el mensaje trae el paso a paso."""
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-sinborrador"}
    )
    assert respuesta.status_code == 404
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "DIAN_DOCUMENT_UNAVAILABLE"
    assert "Diligenciar y presentar" in cuerpo["message"]


async def test_clave_mala_no_gasta_el_expediente(client):
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-bad"}
    )
    assert respuesta.status_code in (401, 422)
    assert respuesta.json()["code"] == "DIAN_INVALID_CREDENTIALS"

    # El expediente sigue listo: una clave mala no lo mueve de estado.
    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    assert detalle["status"] == "DRAFT_READY"


async def test_escribir_exige_sesion(client_sin_sesion):
    respuesta = await client_sin_sesion.post(
        f"{BASE}/00000000-0000-0000-0000-000000000000/portal/escribir",
        json={"dian_password": "x"},
    )
    assert respuesta.status_code == 401
