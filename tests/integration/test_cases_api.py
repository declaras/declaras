"""API del expediente: el recorrido completo que usara la consola del contador y el
agente conversacional, de punta a punta por HTTP."""

from __future__ import annotations

from tests.conftest import wait_for_status


async def test_abrir_un_expediente(client):
    response = await client.post(
        "/v1/cases", json={"id_number": "1020304050", "tax_year": 2025, "full_name": "Ana Perez"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["client"]["full_name"] == "Ana Perez"
    assert body["status"] == "OPEN"
    assert body["documents"] == []


async def test_no_se_puede_abrir_dos_veces_el_mismo_cliente_y_anio(client):
    payload = {"id_number": "1020304050", "tax_year": 2025}
    await client.post("/v1/cases", json=payload)
    second = await client.post("/v1/cases", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "CASE_ALREADY_EXISTS"


async def test_listar_y_consultar_el_detalle(client):
    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = created.json()["id"]

    listado = await client.get("/v1/cases")
    assert any(c["id"] == case_id for c in listado.json())

    detalle = await client.get(f"/v1/cases/{case_id}")
    assert detalle.status_code == 200
    assert detalle.json()["id"] == case_id


async def test_expediente_inexistente_da_404(client):
    response = await client.get("/v1/cases/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "CASE_NOT_FOUND"


async def test_el_cliente_sube_un_documento_por_chat(client):
    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = created.json()["id"]

    response = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": "certificado_intereses_vivienda"},
        files={"file": ("cert.jpg", b"una foto cualquiera", "image/jpeg")},
    )
    assert response.status_code == 200
    doc = response.json()["documents"][0]
    assert doc["doc_type"] == "certificado_intereses_vivienda"
    assert doc["source"] == "CLIENT_UPLOAD"
    assert doc["download_url"]


async def test_flujo_completo_extraccion_dian_vinculada_al_expediente(client):
    """El recorrido real: se abre el expediente, corre una extraccion DIAN (con el
    conector falso), se vincula al expediente, y sus documentos quedan leidos."""
    case_response = await client.post(
        "/v1/cases", json={"id_number": "1020304050", "tax_year": 2025}
    )
    case_id = case_response.json()["id"]

    extraction = await client.post(
        "/v1/extractions",
        json={"id_number": "1020304050", "dian_password": "clave-buena", "tax_year": 2025},
    )
    job_id = extraction.json()["job_id"]
    await wait_for_status(client, job_id, "SUCCEEDED")

    linked = await client.post(f"/v1/cases/{case_id}/link-extraction", json={"job_id": job_id})
    assert linked.status_code == 200
    body = linked.json()
    assert body["status"] == "READY_FOR_REVIEW"
    assert len(body["documents"]) == 5
    doc_types = {d["doc_type"] for d in body["documents"]}
    assert doc_types == {"RUT", "EXOGENA", "PRIOR_RETURN", "SUGGESTED_RETURN", "EINVOICE_SUMMARY"}


async def test_resolver_un_flag(client, container):
    """Genera un flag directo por el contenedor (simulando el que produciria un aviso de
    lectura real) y lo resuelve por la API."""
    from uuid import UUID

    from declaras.domain.case import CaseDocumentSource

    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = UUID(created.json()["id"])

    doc = await container.cases.add_document(
        case_id=case_id,
        doc_type="EXOGENA",
        source=CaseDocumentSource.DIAN_PORTAL,
        storage_uri="file://x",
        filename="e.xlsx",
        content_sha256="abc",
    )
    flag = await container.cases.add_flag(
        case_id=case_id, code="TEST_FLAG", message="revisar esto", source_document_id=doc.id
    )

    response = await client.post(
        f"/v1/cases/{case_id}/flags/{flag.id}/resolve", json={"note": "ya se reviso"}
    )
    assert response.status_code == 200
    assert response.json()["resolved_at"] is not None

    detalle = await client.get(f"/v1/cases/{case_id}")
    assert detalle.json()["open_flags_count"] == 0


async def test_lista_de_clientes_y_sus_expedientes(client):
    await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2024})
    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    client_id = created.json()["client"]["id"]

    clientes = await client.get("/v1/clients")
    assert any(c["id"] == client_id for c in clientes.json())

    expedientes = await client.get(f"/v1/clients/{client_id}/cases")
    assert {c["tax_year"] for c in expedientes.json()} == {2024, 2025}


async def test_requiere_llave_de_api(client):
    response = await client.post(
        "/v1/cases",
        json={"id_number": "1020304050", "tax_year": 2025},
        headers={"X-API-Key": "invalida"},
    )
    assert response.status_code == 401


async def test_la_descarga_ofrece_el_nombre_real_del_documento(client):
    """El almacenamiento nombra los archivos por hash. Sin esto, alguien que descarga su
    declaracion se queda con "e92f38b8ba15.pdf" en la carpeta de descargas."""
    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    case_id = created.json()["id"]
    subido = await client.post(
        f"/v1/cases/{case_id}/documents",
        data={"doc_type": "certificado_intereses_vivienda"},
        files={"file": ("certificado banco.pdf", b"%PDF-1.4", "application/pdf")},
    )

    url = subido.json()["documents"][0]["download_url"]
    # El nombre va en la ruta: el visor de PDF del navegador titula el documento con el ultimo
    # segmento de la URL, y sin esto quien abre su declaracion ve un archivo llamado "content".
    assert "/certificado%20banco.pdf?" in url

    respuesta = await client.get(url)
    assert respuesta.status_code == 200
    assert 'filename="certificado banco.pdf"' in respuesta.headers["content-disposition"]
    assert respuesta.headers["content-type"] == "application/pdf"


async def test_un_nombre_de_archivo_no_puede_inyectar_cabeceras(client):
    """El nombre viaja por la URL, asi que llega del cliente: un salto de linea ahi permitiria
    inyectar otras cabeceras en la respuesta."""
    from declaras.api.routers.documents import safe_filename

    assert safe_filename("a\r\nSet-Cookie: x=1", "seguro.pdf") == "aSet-Cookie x1"
    assert safe_filename('"; evil="', "seguro.pdf") == "evil"
    assert safe_filename("", "seguro.pdf") == "seguro.pdf"
    assert safe_filename("...", "seguro.pdf") == "seguro.pdf"


async def test_el_documento_se_puede_ver_sin_descargarlo(client):
    """Para revisar si un documento es el correcto, bajarlo al disco y abrirlo con otro programa
    es friccion innecesaria."""
    created = await client.post("/v1/cases", json={"id_number": "1020304050", "tax_year": 2025})
    subido = await client.post(
        f"/v1/cases/{created.json()['id']}/documents",
        data={"doc_type": "predial"},
        files={"file": ("predial.pdf", b"%PDF-1.4", "application/pdf")},
    )
    url = subido.json()["documents"][0]["download_url"]

    vista = await client.get(f"{url}&inline=true")
    assert vista.headers["content-disposition"].startswith("inline")
    descarga = await client.get(url)
    assert descarga.headers["content-disposition"].startswith("attachment")
