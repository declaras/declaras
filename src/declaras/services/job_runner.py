"""Worker que consume la cola de jobs.

Corre dentro del mismo proceso de la API (ver nota en credential_vault sobre por que).
Responsabilidades: tomar jobs, reintentar lo reintentable, recuperar arriendos de
workers muertos y limpiar sesiones y credenciales vencidas.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from declaras.config import Settings
from declaras.domain.models import JobKind, JobStatus
from declaras.domain.ports import JobRepository
from declaras.observability import get_logger
from declaras.services.credential_vault import InMemoryCredentialVault
from declaras.services.extraction import ExtractionService
from declaras.services.session_registry import InMemorySessionRegistry

log = get_logger(__name__)

# Cuantas horas de ventanas se conservan. Dos alcanza: la actual y la anterior, por si el
# reloj queda en el borde. Lo demas es basura que nadie va a leer.
_VENTANAS_QUE_SE_GUARDAN = 2

_JANITOR_EVERY_N_TICKS = 15


class JobRunner:
    def __init__(
        self,
        *,
        jobs: JobRepository,
        extraction: ExtractionService,
        vault: InMemoryCredentialVault,
        registry: InMemorySessionRegistry,
        settings: Settings,
        limitador: object | None = None,
    ) -> None:
        self._jobs = jobs
        self._extraction = extraction
        self._vault = vault
        self._registry = registry
        self._settings = settings
        self._limitador = limitador
        self._worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="declaras-job-runner")
        log.info("worker.started", worker_id=self._worker_id)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._registry.close_all()
        log.info("worker.stopped", worker_id=self._worker_id)

    async def _loop(self) -> None:
        ticks = 0
        while not self._stopping.is_set():
            ticks += 1
            try:
                if ticks % _JANITOR_EVERY_N_TICKS == 0:
                    await self._janitor()
                processed = await self._tick()
                if not processed:
                    await asyncio.sleep(self._settings.worker_poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - el loop nunca debe morir
                log.exception("worker.loop_error", error=str(exc)[:200])
                await asyncio.sleep(self._settings.worker_poll_interval_s)

    async def _tick(self) -> bool:
        job = await self._jobs.claim_next(
            kind=JobKind.DIAN_EXTRACTION,
            worker_id=self._worker_id,
            lease_ttl_s=self._settings.worker_lease_ttl_s,
        )
        if job is None:
            return False

        log.info("worker.job_claimed", job_id=str(job.id), attempt=job.attempts)
        await self._extraction.run(job)
        await self._maybe_retry(job.id)
        return True

    async def _maybe_retry(self, job_id: object) -> None:
        """Reencola solo si el error es reintentable y quedan intentos."""
        current = await self._jobs.get(job_id)  # type: ignore[arg-type]
        if current is None or current.status is not JobStatus.FAILED:
            return
        error = current.error or {}
        if not error.get("retryable"):
            return
        if current.attempts >= self._settings.worker_max_attempts:
            log.warning(
                "worker.retries_exhausted", job_id=str(current.id), attempts=current.attempts
            )
            return
        await self._jobs.transition(current.id, status=JobStatus.QUEUED)
        log.info("worker.job_requeued", job_id=str(current.id), attempt=current.attempts)

    async def _janitor(self) -> None:
        released = await self._jobs.release_expired_leases()
        evicted = await self._registry.evict_expired()
        purged = await self._vault.purge_expired()
        # Las ventanas del limitador: una fila por origen y por hora que nada vuelve a leer
        # despues de su hora. Sin barrerlas, la tabla crece para siempre.
        ventanas = 0
        if self._limitador is not None:
            ventanas = await self._limitador.limpiar(  # type: ignore[attr-defined]
                antes_de=datetime.now(UTC) - timedelta(hours=_VENTANAS_QUE_SE_GUARDAN)
            )
        if released or evicted or purged or ventanas:
            log.info(
                "worker.janitor",
                leases_released=released,
                sessions_evicted=evicted,
                credentials_purged=purged,
                ventanas_borradas=ventanas,
            )
