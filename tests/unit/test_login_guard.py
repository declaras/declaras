"""La guarda anti bloqueo es critica: si falla, se bloquean cuentas reales."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.engine import create_schema, create_session_factory
from declaras.adapters.persistence.login_guard import SqlLoginAttemptGuard
from declaras.domain.errors import DianLoginAttemptsExhaustedError


@pytest.fixture
async def guard(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}")
    await create_schema(engine)
    yield SqlLoginAttemptGuard(create_session_factory(engine), max_attempts=2)
    await engine.dispose()


async def test_permite_intentar_cuando_no_hay_fallos(guard):
    await guard.assert_can_attempt("CC-123456")


async def test_bloquea_al_agotar_intentos_antes_que_la_dian(guard):
    subject = "CC-123456"
    assert await guard.register_failure(subject) == 1
    await guard.assert_can_attempt(subject)  # todavia queda uno

    assert await guard.register_failure(subject) == 0
    with pytest.raises(DianLoginAttemptsExhaustedError):
        await guard.assert_can_attempt(subject)


async def test_login_exitoso_limpia_el_contador(guard):
    subject = "CC-999"
    await guard.register_failure(subject)
    await guard.reset(subject)
    await guard.assert_can_attempt(subject)


async def test_los_contadores_son_independientes_por_sujeto(guard):
    await guard.register_failure("CC-111")
    await guard.register_failure("CC-111")
    await guard.assert_can_attempt("CC-222")
