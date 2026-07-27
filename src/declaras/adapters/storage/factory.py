"""Seleccion del backend de almacenamiento segun configuracion."""

from __future__ import annotations

from declaras.config import Settings
from declaras.config.settings import StorageBackend
from declaras.domain.ports import DocumentStore


def build_document_store(settings: Settings) -> DocumentStore:
    if settings.storage_backend is StorageBackend.LOCAL:
        from declaras.adapters.storage.local import LocalDocumentStore

        return LocalDocumentStore(settings.storage_local_root)

    if settings.storage_backend is StorageBackend.GCS:  # pragma: no cover - requiere GCP
        from declaras.adapters.storage.gcs import GcsDocumentStore

        if not settings.storage_gcs_bucket:
            raise ValueError("Falta configurar el bucket de almacenamiento.")
        return GcsDocumentStore(settings.storage_gcs_bucket)

    raise ValueError(f"El almacenamiento {settings.storage_backend} no está soportado.")
