"""Almacenamiento en disco local. Backend por defecto para desarrollo y piloto."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from declaras.adapters.storage.paths import object_key
from declaras.domain.errors import DocumentNotFoundError, StorageError
from declaras.domain.models import RawDocument, StoredDocument, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)


class LocalDocumentStore:
    """Implementa DocumentStore sobre el sistema de archivos."""

    scheme = "file"

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    async def put(
        self, *, taxpayer: TaxpayerRef, document: RawDocument, job_id: UUID
    ) -> StoredDocument:
        sha256 = hashlib.sha256(document.content).hexdigest()
        key = object_key(
            taxpayer=taxpayer,
            doc_type=document.doc_type,
            sha256=sha256,
            filename=document.filename,
            content_type=document.content_type,
            job_id=job_id,
        )
        target = self._root / key
        try:
            await asyncio.to_thread(self._write, target, document.content)
        except OSError as exc:
            raise StorageError(f"no se pudo escribir {key}", key=key) from exc

        log.info(
            "document.stored",
            doc_type=document.doc_type.value,
            size_bytes=len(document.content),
            storage_uri=f"{self.scheme}://{key}",
        )
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
        path = self._resolve(storage_uri)
        if not path.exists():
            raise DocumentNotFoundError(storage_uri=storage_uri)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise StorageError(f"no se pudo leer {storage_uri}") from exc

    async def signed_url(self, storage_uri: str, ttl_seconds: int) -> str | None:
        """El backend local no expone URLs firmadas: se sirve por la API."""
        return None

    # ─────────────────────────── internos ───────────────────────────

    def _write(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(target)

    def _resolve(self, storage_uri: str) -> Path:
        key = storage_uri.removeprefix(f"{self.scheme}://")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError("ruta fuera del almacenamiento", storage_uri=storage_uri)
        return path
