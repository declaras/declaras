"""Guarda anti bloqueo de cuenta.

La DIAN bloquea la cuenta al tercer intento fallido. Este contador vive en base de
datos (no en memoria) para que sobreviva reinicios del proceso: si se pierde la
cuenta, el usuario queda sin poder declarar y eso es un dano real.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import LoginAttemptRow
from declaras.domain.errors import DianLoginAttemptsExhaustedError
from declaras.observability import get_logger

log = get_logger(__name__)


class SqlLoginAttemptGuard:
    """Implementa LoginAttemptGuard."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, max_attempts: int
    ) -> None:
        self._sessions = session_factory
        self._max_attempts = max_attempts

    async def assert_can_attempt(self, subject_key: str) -> None:
        failures = await self._failures(subject_key)
        if failures >= self._max_attempts:
            log.warning("dian.login.attempts_exhausted", failures=failures)
            raise DianLoginAttemptsExhaustedError(
                failures=failures, max_attempts=self._max_attempts
            )

    async def register_failure(self, subject_key: str) -> int:
        async with self._sessions() as session, session.begin():
            row = await session.get(LoginAttemptRow, subject_key)
            if row is None:
                row = LoginAttemptRow(subject_key=subject_key, failures=0)
                session.add(row)
            row.failures += 1
            row.last_failure_at = datetime.now(UTC)
            await session.flush()
            remaining = max(self._max_attempts - row.failures, 0)
        log.warning("dian.login.failure_registered", attempts_remaining=remaining)
        return remaining

    async def reset(self, subject_key: str) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(LoginAttemptRow, subject_key)
            if row is not None:
                row.failures = 0
                row.last_failure_at = None

    async def _failures(self, subject_key: str) -> int:
        async with self._sessions() as session:
            row = await session.get(LoginAttemptRow, subject_key)
            return int(row.failures) if row else 0
