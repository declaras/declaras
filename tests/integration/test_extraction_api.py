"""Pruebas de extremo a extremo de la API, con el conector falso.

Cubren las ramas que el agente que nos consume tiene que saber manejar: exito, clave
mala, intentos agotados, reto de identidad y exito parcial.
"""

from __future__ import annotations

from tests.conftest import wait_for_final_failure, wait_for_status

BASE = "/v1/extractions"


def payload(password: str, **overrides) -> dict:
    body = {
        "id_kind": "CC",
        "id_number": "1020304050",
        "dian_password": password,
        "tax_year": 2025,
    }
    body.update(overrides)
    return body


async def test_extraccion_exitosa_devuelve_los_cinco_documentos(client):
    """Los cinco insumos del calculo, MAS las declaraciones de años anteriores.

    El historial entro despues y en la misma sesion. La razon es que lo escaso no es la
    descarga sino el LOGIN —la DIAN bloquea la cuenta al tercer intento fallido— asi que
    pedirle la clave otra vez para bajar un PDF gasta el recurso caro por ahorrar el barato.
    """
    created = await client.post(BASE, json=payload("clave-buena"))
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "QUEUED"
    assert created.headers["Location"].endswith(body["job_id"])

    final = await wait_for_status(client, body["job_id"], "SUCCEEDED", "FAILED")
    assert final["status"] == "SUCCEEDED"

    doc_types = {doc["doc_type"] for doc in final["documents"]}
    assert {
        "RUT",
        "EXOGENA",
        "PRIOR_RETURN",
        "SUGGESTED_RETURN",
        "EINVOICE_SUMMARY",
    } <= doc_types
    # El historial llega como declaracion presentada, un documento por año.
    historial = [d for d in final["documents"] if d["doc_type"] == "FILED_RETURN"]
    assert len(historial) == 2, "trae los dos ultimos años, no cinco: el camino critico importa"
    # 2022 y 2021: el año anterior (2024) ya vino como PRIOR_RETURN, y 2023 no existe en el
    # falso, que deja ese hueco a proposito.
    assert {d["filename"] for d in historial} == {
        "declaracion-2022.pdf",
        "declaracion-2021.pdf",
    }
    assert not final["failures"]
    assert all(doc["sha256"] and doc["size_bytes"] > 0 for doc in final["documents"])


async def test_los_documentos_se_pueden_descargar(client):
    created = await client.post(BASE, json=payload("clave-buena"))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED")

    download = await client.get(final["documents"][0]["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")


async def test_clave_rechazada_falla_e_informa_intentos_restantes(client):
    created = await client.post(BASE, json=payload("clave-bad"))
    final = await wait_for_status(client, created.json()["job_id"], "FAILED", "SUCCEEDED")

    assert final["status"] == "FAILED"
    assert final["error"]["code"] == "DIAN_INVALID_CREDENTIALS"
    assert final["error"]["retryable"] is False
    assert final["error"]["details"]["attempts_remaining"] == 1


async def test_no_se_encola_cuando_ya_no_quedan_intentos(client):
    for _ in range(2):
        created = await client.post(BASE, json=payload("clave-bad"))
        await wait_for_status(client, created.json()["job_id"], "FAILED")

    blocked = await client.post(BASE, json=payload("clave-bad"))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "DIAN_LOGIN_ATTEMPTS_EXHAUSTED"
    assert blocked.headers["X-Retryable"] == "false"


async def test_reto_de_identidad_parquea_el_job_y_se_reanuda_al_responder(client):
    created = await client.post(BASE, json=payload("clave-challenge"))
    job_id = created.json()["job_id"]

    parked = await wait_for_status(client, job_id, "AWAITING_CHALLENGE", "FAILED")
    assert parked["status"] == "AWAITING_CHALLENGE"
    assert parked["challenge"]["kind"] == "EMAIL_CODE"
    assert "codigo" in parked["challenge"]["prompt"].lower()

    wrong = await client.post(f"{BASE}/{job_id}/challenge", json={"answers": ["0000"]})
    assert wrong.status_code == 401

    accepted = await client.post(f"{BASE}/{job_id}/challenge", json={"answers": ["1234"]})
    assert accepted.status_code == 200

    final = await wait_for_status(client, job_id, "SUCCEEDED", "FAILED")
    assert final["status"] == "SUCCEEDED"
    # Cinco insumos + las dos declaraciones anteriores.
    assert len(final["documents"]) == 7


async def test_documento_no_publicado_produce_exito_parcial(client):
    created = await client.post(BASE, json=payload("clave-noexo"))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED", "FAILED")

    assert final["status"] == "SUCCEEDED"
    # Cuatro insumos (falta la exogena) + las dos declaraciones anteriores.
    assert len(final["documents"]) == 6
    assert len(final["failures"]) == 1
    failure = final["failures"][0]
    assert failure["doc_type"] == "EXOGENA"
    assert failure["code"] == "DIAN_DOCUMENT_UNAVAILABLE"


async def test_portal_caido_es_reintentable(client):
    created = await client.post(BASE, json=payload("clave-down"))
    final = await wait_for_status(client, created.json()["job_id"], "FAILED", "SUCCEEDED")

    assert final["error"]["code"] == "DIAN_PORTAL_UNAVAILABLE"
    assert final["error"]["retryable"] is True


async def test_se_puede_pedir_un_subconjunto_de_documentos(client):
    created = await client.post(BASE, json=payload("ok", doc_types=["RUT", "EXOGENA"]))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED", "FAILED")

    assert {doc["doc_type"] for doc in final["documents"]} == {"RUT", "EXOGENA"}


async def test_no_se_puede_pedir_evidencia_como_documento(client):
    response = await client.post(BASE, json=payload("ok", doc_types=["EVIDENCE"]))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_job_inexistente_devuelve_404(client):
    response = await client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"


async def test_la_api_exige_haber_ingresado(client_sin_sesion):
    response = await client_sin_sesion.post(BASE, json=payload("ok"))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_health_expone_la_configuracion_del_conector(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dian_adapter"] == "fake"


async def test_el_reintento_conserva_las_credenciales(client_con_reintentos):
    """Regresion: si al fallar se borraba la clave, el reintento moria con
    DIAN_SESSION_EXPIRED y ocultaba el error verdadero del portal."""
    created = await client_con_reintentos.post(BASE, json=payload("clave-down"))
    final = await wait_for_final_failure(
        client_con_reintentos, created.json()["job_id"], attempts=2
    )

    assert final["error"]["code"] == "DIAN_PORTAL_UNAVAILABLE", (
        "el reintento debe reportar la falla real del portal, no credenciales ausentes"
    )


async def test_el_anio_gravable_se_deduce_si_no_se_envia(client):
    """El cliente no tiene que saber que anio declara: lo deduce el calendario."""
    from declaras.domain.tax_calendar import default_tax_year

    cuerpo = {"id_number": "1020304050", "dian_password": "ok", "doc_types": ["RUT"]}
    created = await client.post(BASE, json=cuerpo)
    assert created.status_code == 202

    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED", "FAILED")
    assert final["taxpayer"]["tax_year"] == default_tax_year()


async def test_se_puede_pedir_un_anio_anterior_explicito(client):
    """Caso de los atrasados: ponerse al dia con un anio pasado."""
    created = await client.post(BASE, json=payload("ok", tax_year=2023, doc_types=["RUT"]))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED", "FAILED")
    assert final["taxpayer"]["tax_year"] == 2023


# ─────── en que va el trabajo ───────
#
# Contra el portal real una extraccion tarda cerca de medio minuto. Sin decir en que va, quien la
# lanza mira una pantalla quieta y no sabe si funciona, si la clave estaba bien o si se colgo.


def _pasos(respuesta) -> dict[str, str]:
    return {p["key"]: p["state"] for p in respuesta["progress"]}


async def test_los_pasos_se_declaran_completos_desde_el_principio(client):
    """Quien espera tiene que poder ver cuanto falta, y una lista que crece sola no lo dice."""
    created = await client.post(BASE, json=payload("clave-buena"))
    en_curso = created.json()

    # El historial es UN paso y no uno por año: cuantos años tiene la persona solo se sabe
    # despues de preguntarle a la DIAN, y un paso por año seria justamente la lista que crece
    # sola que esta prueba existe para impedir.
    esperados = [
        "login",
        "RUT",
        "EXOGENA",
        "PRIOR_RETURN",
        "SUGGESTED_RETURN",
        "EINVOICE_SUMMARY",
        "historial",
    ]
    assert [p["key"] for p in en_curso["progress"]] == esperados
    # Y con nombre en lenguaje de la persona, no con el codigo del documento.
    assert en_curso["progress"][1]["label"] == "Tu RUT"


async def test_al_terminar_todos_los_pasos_quedan_hechos(client):
    created = await client.post(BASE, json=payload("clave-buena"))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED")

    assert set(_pasos(final).values()) == {"DONE"}


async def test_un_documento_que_la_dian_no_tiene_no_se_marca_como_falla(client):
    """Le pasa a quien declara por primera vez. Marcarlo en rojo asusta sin motivo: se distingue
    "no hay" de "se rompio"."""
    created = await client.post(BASE, json=payload("clave-noexo"))
    final = await wait_for_status(client, created.json()["job_id"], "SUCCEEDED")

    pasos = _pasos(final)
    assert pasos["EXOGENA"] == "EMPTY"
    assert pasos["RUT"] == "DONE"
    assert "FAILED" not in pasos.values()
    # Y el paso dice por que quedo vacio, sin que haya que ir a buscar la falla aparte.
    exogena = next(p for p in final["progress"] if p["key"] == "EXOGENA")
    assert exogena["detail"]


async def test_si_la_clave_esta_mal_se_ve_en_que_paso_se_cayo(client):
    """Era la queja concreta: la pantalla no decia ni si la clave era correcta."""
    created = await client.post(BASE, json=payload("clave-bad"))
    final = await wait_for_status(client, created.json()["job_id"], "FAILED", "SUCCEEDED")

    pasos = _pasos(final)
    assert pasos["login"] == "FAILED"
    assert pasos["RUT"] == "PENDING", "no se intento nada despues de no poder entrar"
    assert final["error"]["code"] == "DIAN_INVALID_CREDENTIALS"


async def test_la_clave_esta_guardada_antes_de_que_el_job_sea_reclamable(client, container):
    """Es la carrera que hizo inestable la prueba de los intentos agotados.

    El insert del job lo deja en cola, y desde ese instante cualquier worker puede reclamarlo.
    Si la clave todavia no esta en la boveda, el worker falla con "sesion expirada" en vez de
    intentar el login, y ese camino no cuenta el intento contra el bloqueo de la cuenta: el
    usuario se queda con intentos que en realidad ya gasto.

    Se comprueba desde la respuesta de encolado, que es lo unico que existe en ese instante: si
    trae el plan completo, el job no se creo a medias.
    """
    created = await client.post(BASE, json=payload("clave-bad"))
    job_id = created.json()["job_id"]

    # Siete: entrar, los cinco documentos y el historial.
    assert len(created.json()["progress"]) == 7, "el job nace con su plan, no lo completa despues"
    from uuid import UUID

    assert await container.vault.get(UUID(job_id)) is not None, "la clave ya tiene que estar"

    final = await wait_for_status(client, job_id, "FAILED")
    # Y falla por lo que de verdad paso, no por credenciales ausentes.
    assert final["error"]["code"] == "DIAN_INVALID_CREDENTIALS"
