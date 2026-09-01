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


async def test_sin_borrador_en_la_cuenta_se_crea_solo(client):
    """La cuenta sin borrador del año es el caso NORMAL de un primerizo, no un error.

    Antes esto devolvia 404 pidiendole al contribuyente que entrara al portal a crear el
    borrador. Eso rompia la promesa entera del tramo: cerrar la declaracion y que lo unico
    que quede sea entrar a firmar. El adaptador lo crea copiando lo que hace la propia app
    de la DIAN (pedir el molde prellenado y mandarlo de vuelta), asi que no queda ningun
    paso manual antes de la firma.
    """
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-sinborrador"}
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["verificado"] is True


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


async def test_el_borrador_queda_para_ver_y_descargar(client):
    """LA PRUEBA DE QUE QUEDO BIEN, en un documento que se puede mirar.

    La verificacion casilla por casilla dice que el portal guardo lo que se mando, pero eso es
    el sistema dandose la razon a si mismo. El PDF que genera la DIAN es lo que un contador
    puede abrir, archivar y mostrarle al cliente, y hasta ahora el proceso terminaba sin el:
    despues de escribir solo quedaba un enlace para ir a mirarlo al portal.

    Se baja en la MISMA sesion de la escritura, que es la unica ventana en que se puede sin
    pedir la clave otra vez.
    """
    case_id = await _listo_para_escribir(client)

    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"}
    )
    assert respuesta.status_code == 200, respuesta.text
    documento_id = respuesta.json()["documento_id"]
    assert documento_id, "el borrador escrito tiene que quedar en el expediente"

    # Y queda como un documento mas: con su nombre, y descargable desde la misma pantalla.
    detalle = (await client.get(f"{BASE}/{case_id}")).json()
    borrador = next(d for d in detalle["documents"] if d["id"] == documento_id)
    assert borrador["doc_type"] == "BORRADOR_ESCRITO"
    assert borrador["filename"] == "borrador-210-2025.pdf"
    assert borrador["download_url"]


async def test_si_el_pdf_no_se_puede_bajar_la_escritura_sigue_valiendo(client, monkeypatch):
    """Son dos cosas distintas y el orden importa: el borrador YA quedo en el portal.

    Reportar la escritura como fallida porque no se pudo bajar su PDF seria mentir al reves, y
    ademas invitaria a escribirlo otra vez, que es una sesion mas contra el portal por un
    documento que se puede bajar despues.
    """
    from declaras.adapters.dian.fake import FakeDianSession

    async def sin_pdf(self, doc_type, taxpayer):
        raise RuntimeError("el portal no entrego el PDF")

    monkeypatch.setattr(FakeDianSession, "download", sin_pdf)

    case_id = await _listo_para_escribir(client)
    respuesta = await client.post(
        f"{BASE}/{case_id}/portal/escribir", json={"dian_password": "clave-buena"}
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["verificado"] is True, "la escritura no depende de haber bajado el PDF"
    assert cuerpo["documento_id"] is None
