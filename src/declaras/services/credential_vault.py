"""Boveda de credenciales en memoria, con vida corta.

REGLA PARA LA EXTRACCION: la clave que llega a extraer documentos no se escribe en disco.
Vive en memoria del proceso, solo el tiempo que dura el trabajo, y se destruye al terminar
o al vencer el TTL. Aca no hay nada que guardar: la clave llega, se usa y sobra.

═══ LA REGLA CAMBIO PARA UN CASO, Y SOLO PARA UNO (2026-08-29) ═══

El embudo de "¿debo declarar?" SI guarda la clave, porque ahi la clave no es de un solo
uso: la persona consulta hoy, decide despues, y volver a pedirsela en cada paso es perder
al cliente entre uno y otro. Es una decision de negocio tomada a conciencia.

Lo que NO cambio: esa clave se guarda CIFRADA (`services/cifrado.py`), con la llave fuera
de la base, y este modulo sigue sin escribir nada. Son dos caminos con dos tratos
distintos, no una excepcion que se derrama sobre el resto.

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
