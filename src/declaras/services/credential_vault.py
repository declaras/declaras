"""Boveda de credenciales en memoria, con vida corta.

REGLA DEL PROYECTO: la clave de la DIAN nunca se escribe en disco ni en base de datos.
Vive en memoria del proceso, solo el tiempo que dura la extraccion, y se destruye al
terminar o al vencer el TTL.

CONSECUENCIA OPERATIVA que hay que respetar al desplegar: como la clave vive en el
proceso, la API y el worker deben ser el mismo proceso (una sola unidad de despliegue).
Para escalar horizontalmente hay que sustituir esta implementacion por una boveda
respaldada por KMS con cifrado en reposo; el puerto no cambia.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from declaras.domain.models import DianCredentials
from declaras.observability import get_logger

log = get_logger(__name__)


class InMemoryCredentialVault:
    """Credenciales por job, con expiracion automatica."""

    def __init__(self, *, ttl_seconds: int = 1_800) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[UUID, tuple[DianCredentials, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, job_id: UUID, credentials: DianCredentials) -> None:
        async with self._lock:
            self._items[job_id] = (credentials, datetime.now(UTC) + self._ttl)

    async def get(self, job_id: UUID) -> DianCredentials | None:
        async with self._lock:
            entry = self._items.get(job_id)
            if entry is None:
                return None
            credentials, expires_at = entry
            if datetime.now(UTC) >= expires_at:
                del self._items[job_id]
                log.info("vault.expired", job_id=str(job_id))
                return None
            return credentials

    async def discard(self, job_id: UUID) -> None:
        async with self._lock:
            self._items.pop(job_id, None)

    async def purge_expired(self) -> int:
        now = datetime.now(UTC)
        async with self._lock:
            expired = [key for key, (_, exp) in self._items.items() if now >= exp]
            for key in expired:
                del self._items[key]
        return len(expired)
