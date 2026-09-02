"""Guarda anti bloqueo de cuenta.

La DIAN bloquea la cuenta al tercer intento fallido. Este contador vive en base de
datos (no en memoria) para que sobreviva reinicios del proceso: si se pierde la
cuenta, el usuario queda sin poder declarar y eso es un dano real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import LoginAttemptRow
from declaras.domain.errors import DianLoginAttemptsExhaustedError
from declaras.observability import get_logger

log = get_logger(__name__)


class SqlLoginAttemptGuard:
    """Implementa LoginAttemptGuard."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int,
        ventana_minutos: int = 15,
    ) -> None:
        self._sessions = session_factory
        self._max_attempts = max_attempts
        # Cuanto duran los fallos antes de caducar. SIN ESTO EL FRENO ES UNA TRAMPA: una vez en
        # el limite, `assert_can_attempt` corta para siempre, y como no deja ni intentar, nunca
        # hay login exitoso que lo resetee. Dos claves mal escritas dejaban la cedula sin poder
        # operar NUNCA MAS, aunque la DIAN nunca la bloqueara (la DIAN corta al tercer fallo;
        # nosotros al segundo, asi que la cuenta real queda a salvo pero la persona queda
        # atrapada de nuestro lado). Con la ventana, tras esperarla se puede reintentar con la
        # clave buena. Es la misma logica de la DIAN, que tampoco cuenta fallos de hace horas.
        self._ventana = timedelta(minutes=ventana_minutos)

    async def assert_can_attempt(self, subject_key: str) -> None:
        failures, ultimo = await self._estado(subject_key)
        # Los fallos caducados no cuentan: si el ultimo fue hace mas que la ventana, la cuenta
        # ya tuvo su descanso y arranca de cero.
        if ultimo is not None and datetime.now(UTC) - ultimo > self._ventana:
            await self.reset(subject_key)
            return
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
        fallos, _ = await self._estado(subject_key)
        return fallos

    async def _estado(self, subject_key: str) -> tuple[int, datetime | None]:
        async with self._sessions() as session:
            row = await session.get(LoginAttemptRow, subject_key)
            if row is None:
                return 0, None
            ultimo = row.last_failure_at
            if ultimo is not None and ultimo.tzinfo is None:
                # SQLite devuelve naive; se ancla a UTC, que es como se guardo.
                ultimo = ultimo.replace(tzinfo=UTC)
            return int(row.failures), ultimo
