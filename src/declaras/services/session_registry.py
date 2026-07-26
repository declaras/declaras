"""Registro de sesiones vivas que esperan la respuesta del contribuyente.

Sostiene el patron relevo: cuando el portal pide un codigo del correo, la sesion del
navegador debe seguir abierta mientras el usuario lo consulta y responde por WhatsApp.
Volver a hacer login desde cero no sirve, porque el portal reiniciaria el reto.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from declaras.domain.ports import DianSession
from declaras.observability import get_logger

log = get_logger(__name__)


class InMemorySessionRegistry:
    """Sesiones parqueadas por job, con TTL para no dejar navegadores colgados."""

    def __init__(self, *, ttl_seconds: int = 900) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[UUID, tuple[DianSession, datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, job_id: UUID, session: DianSession) -> None:
        async with self._lock:
            self._items[job_id] = (session, datetime.now(UTC) + self._ttl)
        log.info("session_registry.parked", job_id=str(job_id))

    async def get(self, job_id: UUID) -> DianSession | None:
        async with self._lock:
            entry = self._items.get(job_id)
            if entry is None:
                return None
            session, expires_at = entry
            if datetime.now(UTC) >= expires_at:
                del self._items[job_id]
                await self._safe_close(session)
                return None
            return session

    async def discard(self, job_id: UUID) -> None:
        async with self._lock:
            self._items.pop(job_id, None)

    async def evict_expired(self) -> int:
        now = datetime.now(UTC)
        async with self._lock:
            expired = [(key, sess) for key, (sess, exp) in self._items.items() if now >= exp]
            for key, _ in expired:
                del self._items[key]
        for _, session in expired:
            await self._safe_close(session)
        if expired:
            log.info("session_registry.evicted", count=len(expired))
        return len(expired)

    async def close_all(self) -> None:
        async with self._lock:
            sessions = [sess for sess, _ in self._items.values()]
            self._items.clear()
        for session in sessions:
            await self._safe_close(session)

    async def _safe_close(self, session: DianSession) -> None:
        try:
            await session.close()
        except Exception as exc:  # pragma: no cover
            log.warning("session_registry.close_failed", error=str(exc)[:200])
