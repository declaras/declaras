"""Almacenamiento en Google Cloud Storage. Backend de produccion.

Requiere el extra `gcs`: uv sync --extra gcs
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from declaras.adapters.storage.paths import object_key
from declaras.domain.errors import DocumentNotFoundError, StorageError
from declaras.domain.models import RawDocument, StoredDocument, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)


class GcsDocumentStore:
    """Implementa DocumentStore sobre un bucket de GCS."""

    scheme = "gs"

    def __init__(self, bucket_name: str) -> None:
        try:
            from google.cloud import storage
        except ImportError as exc:  # pragma: no cover
            raise StorageError("Falta instalar el soporte de almacenamiento en la nube.") from exc
        self._bucket_name = bucket_name
        self._bucket = storage.Client().bucket(bucket_name)

    async def put(
        self, *, taxpayer: TaxpayerRef, document: RawDocument, scope_id: UUID
    ) -> StoredDocument:
        sha256 = hashlib.sha256(document.content).hexdigest()
        key = object_key(
            taxpayer=taxpayer,
            doc_type=document.doc_type,
            sha256=sha256,
            filename=document.filename,
            content_type=document.content_type,
            scope_id=scope_id,
        )
        try:
            await asyncio.to_thread(self._upload, key, document)
        except Exception as exc:  # pragma: no cover - depende de red
            raise StorageError(f"No se pudo guardar el documento {key}.", key=key) from exc

        log.info("document.stored", doc_type=document.doc_type.value, storage_uri=f"gs://{key}")
        return StoredDocument(
            doc_type=document.doc_type,
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=len(document.content),
            sha256=sha256,
            storage_uri=f"{self.scheme}://{key}",
            captured_at=datetime.now(UTC),
            source_url=document.source_url,
            metadata=document.metadata,
        )

    async def read(self, storage_uri: str) -> bytes:
        blob = self._bucket.blob(self._key_of(storage_uri))
        exists = await asyncio.to_thread(blob.exists)
        if not exists:
            raise DocumentNotFoundError(storage_uri=storage_uri)
        content: bytes = await asyncio.to_thread(blob.download_as_bytes)
        return content

    async def signed_url(self, storage_uri: str, ttl_seconds: int) -> str | None:
        blob = self._bucket.blob(self._key_of(storage_uri))
        return await asyncio.to_thread(
            blob.generate_signed_url, expiration=timedelta(seconds=ttl_seconds), version="v4"
        )

    def _upload(self, key: str, document: RawDocument) -> None:
        blob = self._bucket.blob(key)
        blob.upload_from_string(document.content, content_type=document.content_type)

    def _key_of(self, storage_uri: str) -> str:
        return storage_uri.removeprefix(f"{self.scheme}://")
