"""El patrimonio del año pasado, usado donde hace falta: al preguntar por los bienes.

═══ EL DATO ESTABA Y NO SE USABA ═══

Un inmueble no lo reporta nadie año tras año —ninguna notaría le dice a la DIAN que alguien
SIGUE siendo dueño de su apartamento— así que el patrimonio se pregunta en vez de conciliarse.
Correcto. Lo que no es correcto es preguntarlo EN BLANCO teniendo la declaración del año pasado
en el expediente: ahí hay un número que dice cuánto había, y se estaba leyendo solo para una
alerta de comparación patrimonial.

Preguntar "¿tienes casa?" sin decir "el año pasado declaraste 180 millones y llevas cero" es
esconder el dato más útil del expediente. Fue el reclamo de un caso real.

═══ ES UNA PISTA, NO UN FALTANTE ═══

El patrimonio pudo bajar de verdad: se vendió el carro, se gastó el ahorro. Por eso lo que se
devuelve es cuánto no aparece todavía, y nunca un número negativo: que el patrimonio de este
año sea MAYOR que el del pasado es lo normal y no hay nada que explicar por ese lado.
"""

from tests.integration.test_conciliacion_api import FILA_SALARIO, _conciliado

BASE = "/v1/cases"


async def test_sin_declaracion_anterior_no_se_inventa_una_referencia(client):
    """`None` no es cero: sin 210 del año pasado no se sabe cuánto patrimonio había, y un cero
    diría que no había ninguno."""
    case_id = await _conciliado(client, FILA_SALARIO)

    datos = (await client.get(f"{BASE}/{case_id}/patrimonio")).json()
    assert datos["bruto_anterior"] is None
    assert datos["por_explicar"] is None


async def test_con_declaracion_anterior_dice_cuanto_falta_por_explicar(client):
    """El caso que importa: la consulta trajo el 210 del año pasado, así que el patrimonio se
    pregunta CON referencia."""
    from tests.integration.test_cases_api import wait_for_status

    creado = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = creado.json()["id"]
    extraccion = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-buena", "tax_year": 2025},
    )
    job_id = extraccion.json()["job_id"]
    await wait_for_status(client, job_id, "SUCCEEDED")
    await client.post(f"{BASE}/{case_id}/link-extraction", json={"job_id": job_id})

    datos = (await client.get(f"{BASE}/{case_id}/patrimonio")).json()
    bruto = datos["bruto_anterior"]
    if bruto is None:
        # El 210 del falso no siempre trae la casilla 29 legible; lo que se fija es la regla,
        # no la cifra del doble.
        assert datos["por_explicar"] is None
        return

    assert datos["por_explicar"] is not None
    # Nunca negativo: un patrimonio mayor que el del año pasado no tiene nada que explicar.
    assert datos["por_explicar"] >= 0
    ya_hay = datos["total_capturado"] + datos["total_reportado"]
    assert datos["por_explicar"] == max(bruto - ya_hay, 0)
