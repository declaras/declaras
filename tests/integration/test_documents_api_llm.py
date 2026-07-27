"""Los dos endpoints de lectura, con un lector de la familia con modelo detrás.

Lo que se prueba no es el lector (eso vive en las pruebas unitarias) sino que la request no
se lleve el event loop consigo: los cuatro parsers determinísticos tardan milisegundos, y con
ellos el `read` síncrono era inocuo; una llamada a un modelo tarda decenas de segundos.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest

from declaras.documents import registry
from declaras.documents.models import DocumentReading
from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef

DOC_TYPE = "CERT_INGRESOS_220"


@pytest.fixture
def hilos_del_lector(monkeypatch) -> list[int]:
    """Registra un lector con modelo que solo anota en qué hilo lo llamaron."""
    hilos: list[int] = []

    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        hilos.append(threading.get_ident())
        return DocumentReading(doc_type=DOC_TYPE, parser="falso", content_sha256="abc")

    monkeypatch.setitem(registry.LLM_READERS, DOC_TYPE, lector)
    return hilos


async def test_leer_un_documento_subido_no_bloquea_el_event_loop(client, hilos_del_lector):
    response = await client.post(
        "/v1/documents/read",
        data={"doc_type": DOC_TYPE, "anio_esperado": 2025},
        files={"file": ("220.pdf", b"%PDF-x", "application/pdf")},
    )
    assert response.status_code == 200
    assert hilos_del_lector and hilos_del_lector[0] != threading.get_ident()


async def test_leer_un_documento_almacenado_no_bloquea_el_event_loop(
    client, container, hilos_del_lector
):
    stored = await container.store.put(
        taxpayer=TaxpayerRef(id_number="1020304050", tax_year=2025),
        document=RawDocument(
            doc_type=DocumentType.CLIENT_DOCUMENT, filename="220.pdf", content=b"%PDF-x"
        ),
        scope_id=uuid4(),
    )

    response = await client.post(
        "/v1/documents/read-stored",
        json={"storage_uri": stored.storage_uri, "doc_type": DOC_TYPE},
    )
    assert response.status_code == 200
    assert hilos_del_lector and hilos_del_lector[0] != threading.get_ident()
