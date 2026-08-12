"""El patrimonio que nadie reporta: la casa, el carro, la moto.

Estas pruebas cubren un agujero que era estructural y no de cifras. `a_caso` recibía el parámetro
`patrimonio` desde el principio y NADIE se lo pasaba: no había ninguna ruta —ni siquiera manual—
por la que un inmueble llegara a la casilla 29. El síntoma no era una casilla corta sino algo peor,
porque el patrimonio bruto es uno de los cinco topes del art. 592: un patrimonio incompleto puede
decirle a alguien que no está obligado a declarar cuando sí lo está.
"""

from tests.integration.test_conciliacion_api import (
    FILA_SALARIO,
    _conciliado,
    _sin_patrimonio,
)

BASE = "/v1/cases"


async def _guardar(client, case_id: str, **campos) -> dict:
    payload = {"id": campos.pop("id", "bien-1"), "quien": "cliente", **campos}
    respuesta = await client.post(f"{BASE}/{case_id}/patrimonio/bienes", json=payload)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def _casilla(client, case_id: str, numero: int) -> int:
    respuesta = await client.get(f"{BASE}/{case_id}/formulario")
    assert respuesta.status_code == 200, respuesta.text
    return next(c["valor"] for c in respuesta.json() if c["numero"] == numero)


async def test_un_inmueble_capturado_llega_a_la_casilla_29(client):
    """El caso que no tenía ruta: la casa entra al patrimonio bruto."""
    case_id = await _conciliado(client, FILA_SALARIO)
    antes = await _casilla(client, case_id, 29)

    await _guardar(
        client,
        case_id,
        tipo="inmueble",
        descripcion="Apartamento 502",
        avaluo_catastral=180_000_000,
    )

    assert await _casilla(client, case_id, 29) == antes + 180_000_000


async def test_el_inmueble_se_declara_por_el_mayor_de_los_dos_valores(client):
    """Art. 277, el mayor entre el costo de adquisición y el avalúo. Los dos sentidos, porque un
    máximo mal escrito pasa la mitad de los casos."""
    case_id = await _conciliado(client, FILA_SALARIO)

    caro_el_avaluo = await _guardar(
        client,
        case_id,
        id="compro-barato",
        tipo="inmueble",
        descripcion="Casa vieja",
        costo_adquisicion=80_000_000,
        avaluo_catastral=200_000_000,
    )
    bien = next(b for b in caro_el_avaluo["bienes"] if b["id"] == "compro-barato")
    assert bien["valor"] == 200_000_000
    assert bien["regla"] == "el avalúo del predial, que es mayor que el precio de compra"
    assert bien["norma"] == "art. 277 ET"

    caro_el_costo = await _guardar(
        client,
        case_id,
        id="compro-caro",
        tipo="inmueble",
        descripcion="Apto nuevo",
        costo_adquisicion=450_000_000,
        avaluo_catastral=300_000_000,
    )
    bien = next(b for b in caro_el_costo["bienes"] if b["id"] == "compro-caro")
    assert bien["valor"] == 450_000_000
    assert bien["regla"] == "lo que costó, que es mayor que el avalúo del predial"


async def test_con_un_solo_valor_el_inmueble_entra_pero_avisa(client):
    """Entra igual —dejarlo fuera subdeclararía, que es lo que la sanción castiga— y queda
    señalado, porque con un solo candidato no se puede afirmar que sea el mayor."""
    case_id = await _conciliado(client, FILA_SALARIO)
    vista = await _guardar(
        client,
        case_id,
        tipo="inmueble",
        descripcion="Lote",
        avaluo_catastral=50_000_000,
    )
    bien = vista["bienes"][0]
    assert bien["valor"] == 50_000_000
    assert bien["falta"] is not None
    assert "precio de compra" in bien["falta"]


async def test_el_vehiculo_se_valora_por_el_costo_y_no_por_el_avaluo(client):
    """Art. 267, costo fiscal. El recibo del impuesto vehicular es el papel que todo el mundo
    tiene a mano y NO sirve para esto, así que sin el valor de compra no hay cifra y se dice."""
    case_id = await _conciliado(client, FILA_SALARIO)

    sin_costo = await _guardar(
        client, case_id, id="moto", tipo="vehiculo", descripcion="Moto AKT 125", cilindraje=125
    )
    bien = next(b for b in sin_costo["bienes"] if b["id"] == "moto")
    assert bien["valor"] == 0
    assert "impuesto vehicular" in bien["falta"]

    con_costo = await _guardar(
        client,
        case_id,
        id="moto",
        tipo="vehiculo",
        descripcion="Moto AKT 125",
        cilindraje=125,
        costo_adquisicion=7_500_000,
    )
    bien = next(b for b in con_costo["bienes"] if b["id"] == "moto")
    assert bien["valor"] == 7_500_000
    assert bien["falta"] is None
    # El mismo id CORRIGE en vez de duplicar: capturar sin el papel y completar cuando llega es el
    # caso normal de esta pantalla, y con solo alta y baja cada corrección dejaría un duplicado.
    assert len([b for b in con_costo["bienes"] if b["id"] == "moto"]) == 1


async def test_la_deuda_del_bien_baja_el_patrimonio_liquido(client):
    """Capturar la casa sin la hipoteca no es medio dato: infla el patrimonio líquido y dispara
    la alerta de comparación patrimonial (art. 236) en todo caso con inmueble financiado."""
    case_id = await _conciliado(client, FILA_SALARIO)
    liquido_antes = await _casilla(client, case_id, 31)

    await _guardar(
        client,
        case_id,
        tipo="inmueble",
        descripcion="Apto financiado",
        avaluo_catastral=300_000_000,
        deuda_saldo=120_000_000,
        deuda_acreedor="Bancolombia",
    )

    assert await _casilla(client, case_id, 30) == 120_000_000
    assert await _casilla(client, case_id, 31) == liquido_antes + 180_000_000


async def test_el_patrimonio_sin_contestar_no_deja_cerrar_el_borrador(client):
    """La compuerta. No es una alerta de la liquidación a propósito: una alerta bloqueante apaga
    el optimizador, y como el patrimonio arranca sin contestar en todos los casos, el preliminar
    habría quedado siempre sin optimizar y la ganancia que se le muestra al cliente saldría
    inflada por un optimizador apagado, no por un ahorro."""
    case_id = await _conciliado(client, FILA_SALARIO)

    bloqueado = await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")
    assert bloqueado.status_code == 409
    cuerpo = bloqueado.json()
    assert cuerpo["code"] == "LIQUIDACION_BLOQUEADA"
    codigos = {d["codigo"] for d in cuerpo["details"]["detalles"]}
    assert codigos == {"PATRIMONIO_INCOMPLETO"}
    # Enumera QUÉ falta, no solo que falta: un bloqueo sin letrero es una puerta cerrada.
    assert any("carro o moto" in d["mensaje"] for d in cuerpo["details"]["detalles"])

    await _sin_patrimonio(client, case_id)
    assert (await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")).status_code == 200


async def test_decir_que_si_y_no_cargar_nada_tampoco_deja_cerrar(client):
    """El estado a medio camino, que es el que más se le olvida a la gente: contestó "sí tengo
    carro" y ahí quedó. Sin esto el expediente parecería completo con el vehículo por fuera."""
    case_id = await _conciliado(client, FILA_SALARIO)
    await _sin_patrimonio(client, case_id)
    await client.post(
        f"{BASE}/{case_id}/respuestas",
        json={"pregunta": "VEHICULOS", "tiene": True, "quien": "cliente"},
    )

    bloqueado = await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")
    assert bloqueado.status_code == 409
    assert any(
        "no hay ninguno cargado" in d["mensaje"] for d in bloqueado.json()["details"]["detalles"]
    )

    await _guardar(
        client, case_id, tipo="vehiculo", descripcion="Mazda 3", costo_adquisicion=60_000_000
    )
    assert (await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")).status_code == 200


async def test_la_vista_separa_lo_que_ya_reporta_la_exogena_de_lo_que_hay_que_preguntar(client):
    """Las dos mitades van juntas en la respuesta pero NO revueltas: la pregunta más cara del
    cuestionario es la que se hace por algo que el sistema ya tenía contado."""
    case_id = await _conciliado(client, FILA_SALARIO)
    await _guardar(
        client, case_id, tipo="vehiculo", descripcion="Mazda 3", costo_adquisicion=60_000_000
    )

    vista = (await client.get(f"{BASE}/{case_id}/patrimonio")).json()
    assert vista["total_capturado"] == 60_000_000
    assert vista["total_bruto"] == vista["total_capturado"] + vista["total_reportado"]
    assert [b["descripcion"] for b in vista["bienes"]] == ["Mazda 3"]
    assert all("Mazda" not in r["descripcion"] for r in vista["reportados"])
    assert {p["pregunta"] for p in vista["preguntas"]} == {
        "INMUEBLES",
        "VEHICULOS",
        "OTROS_BIENES",
    }
    # `None` es "sin contestar", que no es lo mismo que "contestó que no": la diferencia decide si
    # se le vuelve a preguntar al cliente.
    assert all(p["contestada"] is None for p in vista["preguntas"])


async def test_un_bien_se_puede_quitar(client):
    case_id = await _conciliado(client, FILA_SALARIO)
    await _guardar(client, case_id, id="carro", tipo="vehiculo", descripcion="Mazda 3")
    vista = await client.delete(f"{BASE}/{case_id}/patrimonio/bienes/carro")
    assert vista.status_code == 200
    assert vista.json()["bienes"] == []


async def test_ver_el_patrimonio_exige_haber_ingresado(client_sin_sesion, client):
    case_id = await _conciliado(client, FILA_SALARIO)
    respuesta = await client_sin_sesion.get(f"{BASE}/{case_id}/patrimonio")
    assert respuesta.status_code == 401


async def test_un_bien_cargado_contesta_su_propia_pregunta(client):
    """Un hecho vale más que la respuesta al hecho. Con un apartamento en el expediente, decir
    "falta contestar si tiene inmuebles" es absurdo para quien lo lee, y bloqueaba el cierre por
    una pregunta que la realidad ya había contestado (el caso del bien capturado por fuera del
    cuestionario: el agente por WhatsApp, una carga del contador)."""
    case_id = await _conciliado(client, FILA_SALARIO)
    await client.post(
        f"{BASE}/{case_id}/respuestas",
        json={"pregunta": "VEHICULOS", "tiene": False, "quien": "cliente"},
    )
    await client.post(
        f"{BASE}/{case_id}/respuestas",
        json={"pregunta": "OTROS_BIENES", "tiene": False, "quien": "cliente"},
    )
    # Nadie contestó la de inmuebles, pero hay uno cargado.
    await _guardar(
        client, case_id, tipo="inmueble", descripcion="Apto 502", avaluo_catastral=180_000_000
    )

    vista = (await client.get(f"{BASE}/{case_id}/patrimonio")).json()
    assert vista["falta"] == []
    assert vista["completo"] is True
    assert (await client.post(f"{BASE}/{case_id}/liquidacion/cerrar")).status_code == 200
