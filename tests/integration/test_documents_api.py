"""Endpoints de lectura de documentos: los dos caminos de entrada."""

from __future__ import annotations

from tests.documents_fixtures import build_exogena_xlsx


async def test_lista_los_tipos_soportados(client):
    response = await client.get("/v1/documents/types")
    assert response.status_code == 200
    assert "EXOGENA" in response.json()
    assert "RUT" in response.json()


async def test_lee_un_documento_subido_directamente(client):
    """Es el camino que usa el agente cuando el cliente manda una foto o un PDF."""
    content = build_exogena_xlsx(id_number="7788990011")
    response = await client.post(
        "/v1/documents/read",
        data={"doc_type": "EXOGENA"},
        files={"file": ("exogena.xlsx", content, "application/octet-stream")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "EXOGENA"
    campos = {f["name"]: f["value"] for f in body["fields"]}
    assert campos["id_number"] == "7788990011"


async def test_lee_un_documento_ya_almacenado_por_el_conector(client, container):
    """Encadena la extraccion del portal con la lectura estructurada, sin bajar de nuevo
    el archivo: el agente conversacional pasa el storage_uri que ya le devolvio la
    extraccion."""
    from uuid import uuid4

    from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef

    stored = await container.store.put(
        taxpayer=TaxpayerRef(id_number="1020304050", tax_year=2025),
        document=RawDocument(
            doc_type=DocumentType.EXOGENA,
            filename="exogena.xlsx",
            content=build_exogena_xlsx(taxpayer_name="RESTREPO VELEZ"),
        ),
        scope_id=uuid4(),
    )

    response = await client.post(
        "/v1/documents/read-stored",
        json={"storage_uri": stored.storage_uri, "doc_type": "EXOGENA"},
    )
    assert response.status_code == 200
    campos = {f["name"]: f["value"] for f in response.json()["fields"]}
    assert campos["taxpayer_name"] == "RESTREPO VELEZ"


async def test_documento_de_un_tipo_no_soportado_devuelve_422(client):
    response = await client.post(
        "/v1/documents/read",
        data={"doc_type": "PASAPORTE_EXTRANJERO"},
        files={"file": ("x.bin", b"contenido", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_DOCUMENT_TYPE"


async def test_un_documento_corrupto_se_distingue_de_uno_sin_lector(client):
    """El agente necesita distinguir los dos casos: uno se resuelve pidiendo el documento
    de nuevo, el otro es una limitacion del sistema y no tiene sentido reintentarlo."""
    response = await client.post(
        "/v1/documents/read",
        data={"doc_type": "EXOGENA"},
        files={"file": ("roto.xlsx", b"esto no es un xlsx", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "DOCUMENT_UNREADABLE"


async def test_requiere_llave_de_api(client):
    response = await client.post(
        "/v1/documents/read",
        data={"doc_type": "EXOGENA"},
        files={"file": ("x.xlsx", build_exogena_xlsx(), "application/octet-stream")},
        headers={"X-API-Key": "invalida"},
    )
    assert response.status_code == 401


async def test_sin_tipo_el_documento_se_clasifica_y_se_rutea(client, monkeypatch):
    """El tipo es opcional: quien manda cuatro PDF juntos sin decir qué es cada uno no
    tiene que adivinar. El camino normal SÍ informa el tipo — el flujo del producto sabe
    qué documento pidió — y esto es para el otro caso."""
    import declaras.api.routers.documents_read as router

    monkeypatch.setattr(router, "detectar_tipo", lambda _content: "EXOGENA")
    response = await client.post(
        "/v1/documents/read",
        files={
            "file": (
                "quien_sabe.xlsx",
                build_exogena_xlsx(id_number="1122334455"),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["doc_type"] == "EXOGENA"


async def test_un_documento_que_no_se_puede_clasificar_pide_el_tipo(client, monkeypatch):
    """No se adivina un default. Clasificar mal mete la cifra en el renglón equivocado del
    formulario; preguntar cuesta una pregunta."""
    import declaras.api.routers.documents_read as router

    monkeypatch.setattr(router, "detectar_tipo", lambda _content: router.DESCONOCIDO)
    response = await client.post(
        "/v1/documents/read",
        files={"file": ("misterio.pdf", b"%PDF-1.7 algo", "application/octet-stream")},
    )
    assert response.status_code == 422
    cuerpo = response.json()
    assert cuerpo["code"] == "VALIDATION_ERROR"
    assert "EXOGENA" in cuerpo["details"]["supported"]
