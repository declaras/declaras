"""Repositorio de jobs sobre SQLAlchemy.

El punto delicado es `claim_next`: dos workers no pueden tomar el mismo job. Se
resuelve con una actualizacion condicional (compare-and-set) que es portable entre
SQLite y Postgres, en lugar de depender de SELECT FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import JobRow
from declaras.domain.errors import JobNotFoundError
from declaras.domain.models import IdentityChallenge, Job, JobKind, JobStatus

_CLAIM_RETRIES = 3


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite devuelve datetimes sin zona: se asumen UTC para no comparar peras con manzanas."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _to_domain(row: JobRow) -> Job:
    return Job(
        id=UUID(row.id),
        kind=JobKind(row.kind),
        status=JobStatus(row.status),
        request=row.request or {},
        result=row.result,
        error=row.error,
        challenge=IdentityChallenge.model_validate(row.challenge) if row.challenge else None,
        attempts=row.attempts,
        created_at=_as_utc(row.created_at) or _utcnow(),
        updated_at=_as_utc(row.updated_at) or _utcnow(),
        leased_until=_as_utc(row.leased_until),
        worker_id=row.worker_id,
    )


class SqlJobRepository:
    """Implementa JobRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, *, kind: JobKind, request: dict[str, Any]) -> Job:
        now = _utcnow()
        row = JobRow(
            id=str(uuid4()),
            kind=kind.value,
            status=JobStatus.QUEUED.value,
            request=request,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        async with self._sessions() as session, session.begin():
            session.add(row)
        return _to_domain(row)

    async def get(self, job_id: UUID) -> Job | None:
        async with self._sessions() as session:
            row = await session.get(JobRow, str(job_id))
            return _to_domain(row) if row else None

    async def claim_next(self, *, kind: JobKind, worker_id: str, lease_ttl_s: int) -> Job | None:
        for _ in range(_CLAIM_RETRIES):
            now = _utcnow()
            async with self._sessions() as session, session.begin():
                candidate = (
                    await session.execute(
                        select(JobRow)
                        .where(JobRow.kind == kind.value, JobRow.status == JobStatus.QUEUED.value)
                        .order_by(JobRow.created_at)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if candidate is None:
                    return None

                claim_stmt = (
                    update(JobRow)
                    .where(JobRow.id == candidate.id, JobRow.status == JobStatus.QUEUED.value)
                    .values(
                        status=JobStatus.RUNNING.value,
                        worker_id=worker_id,
                        leased_until=now + timedelta(seconds=lease_ttl_s),
                        attempts=JobRow.attempts + 1,
                        updated_at=now,
                    )
                )
                claimed = cast(CursorResult[Any], await session.execute(claim_stmt))
                if claimed.rowcount == 1:
                    row = await session.get(JobRow, candidate.id)
                    return _to_domain(row) if row else None
        return None

    async def heartbeat(self, job_id: UUID, *, lease_ttl_s: int) -> None:
        now = _utcnow()
        await self._update(
            job_id, leased_until=now + timedelta(seconds=lease_ttl_s), updated_at=now
        )

    async def mark_succeeded(self, job_id: UUID, *, result: dict[str, Any]) -> Job:
        return await self._update(
            job_id,
            status=JobStatus.SUCCEEDED.value,
            result=result,
            error=None,
            challenge=None,
            leased_until=None,
            updated_at=_utcnow(),
        )

    async def mark_failed(self, job_id: UUID, *, error: dict[str, Any]) -> Job:
        return await self._update(
            job_id,
            status=JobStatus.FAILED.value,
            error=error,
            leased_until=None,
            updated_at=_utcnow(),
        )

    async def mark_awaiting_challenge(self, job_id: UUID, *, challenge: IdentityChallenge) -> Job:
        return await self._update(
            job_id,
            status=JobStatus.AWAITING_CHALLENGE.value,
            challenge=challenge.model_dump(mode="json"),
            updated_at=_utcnow(),
        )

    async def transition(self, job_id: UUID, *, status: JobStatus) -> Job:
        return await self._update(job_id, status=status.value, updated_at=_utcnow())

    async def release_expired_leases(self) -> int:
        """Reencola jobs cuyo worker murio sin cerrar el arriendo."""
        now = _utcnow()
        async with self._sessions() as session, session.begin():
            statement = (
                update(JobRow)
                .where(
                    JobRow.status == JobStatus.RUNNING.value,
                    JobRow.leased_until.is_not(None),
                    JobRow.leased_until < now,
                )
                .values(
                    status=JobStatus.QUEUED.value,
                    worker_id=None,
                    leased_until=None,
                    updated_at=now,
                )
            )
            result = cast(CursorResult[Any], await session.execute(statement))
            return int(result.rowcount or 0)

    async def _update(self, job_id: UUID, **values: Any) -> Job:
        async with self._sessions() as session, session.begin():
            row = await session.get(JobRow, str(job_id))
            if row is None:
                raise JobNotFoundError(job_id=str(job_id))
            for key, value in values.items():
                setattr(row, key, value)
            await session.flush()
            return _to_domain(row)
