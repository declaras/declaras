"""Almacenamiento: rutas sin datos personales en claro y proteccion de traversal."""

from __future__ import annotations

from uuid import uuid4

import pytest

from declaras.adapters.storage.local import LocalDocumentStore
from declaras.adapters.storage.paths import object_key, subject_dir
from declaras.domain.errors import StorageError
from declaras.domain.models import DocumentType, RawDocument, TaxpayerRef


@pytest.fixture
def taxpayer() -> TaxpayerRef:
    return TaxpayerRef(id_number="1020304050", tax_year=2025)


def test_la_ruta_no_contiene_el_numero_de_documento(taxpayer):
    assert "1020304050" not in subject_dir(taxpayer)


def test_la_llave_agrupa_por_sujeto_anio_y_tipo(taxpayer):
    key = object_key(
        taxpayer=taxpayer,
        doc_type=DocumentType.RUT,
        sha256="a" * 64,
        filename="rut.pdf",
        content_type="application/pdf",
        job_id=uuid4(),
    )
    assert "/2025/rut/" in key
    assert key.endswith(".pdf")


async def test_guardar_y_leer_documento(tmp_path, taxpayer):
    store = LocalDocumentStore(tmp_path)
    document = RawDocument(doc_type=DocumentType.RUT, filename="rut.pdf", content=b"%PDF-fake")
    stored = await store.put(taxpayer=taxpayer, document=document, job_id=uuid4())

    assert stored.size_bytes == len(b"%PDF-fake")
    assert stored.sha256
    assert await store.read(stored.storage_uri) == b"%PDF-fake"


async def test_rechaza_rutas_fuera_del_almacenamiento(tmp_path):
    store = LocalDocumentStore(tmp_path)
    with pytest.raises(StorageError):
        await store.read("file://../../../etc/passwd")
