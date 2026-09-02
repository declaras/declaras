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


async def test_los_fallos_caducan_y_no_dejan_a_la_cedula_atrapada(tmp_path):
    """SIN VENTANA EL FRENO ES UNA TRAMPA. Corta al segundo fallo para no llegar al tercero
    —que es el que bloquea la cuenta en la DIAN— pero sin caducidad se quedaba cortando PARA
    SIEMPRE: como no deja ni intentar, nunca hay login exitoso que lo resetee. Dos claves mal
    escritas dejaban la cédula sin poder operar nunca más, aunque la DIAN nunca la bloqueara.

    Con una ventana de un instante, los fallos de 'hace un rato' ya no cuentan y la persona
    puede volver a intentar con la clave buena.
    """
    from declaras.adapters.persistence.engine import create_schema, create_session_factory

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v.db'}")
    await create_schema(engine)
    # Ventana de 0 minutos: cualquier fallo pasado ya está caducado.
    guard = SqlLoginAttemptGuard(create_session_factory(engine), max_attempts=2, ventana_minutos=0)

    await guard.register_failure("CC-123")
    await guard.register_failure("CC-123")
    # Con la ventana en cero, el fallo de recién ya caducó: deja intentar de nuevo en vez de
    # lanzar EXHAUSTED.
    await guard.assert_can_attempt("CC-123")  # no debe lanzar
    await engine.dispose()


async def test_dentro_de_la_ventana_si_corta(tmp_path):
    """La caducidad no puede volver el freno inútil: dentro de la ventana, dos fallos cortan."""
    from declaras.adapters.persistence.engine import create_schema, create_session_factory

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
    await create_schema(engine)
    guard = SqlLoginAttemptGuard(
        create_session_factory(engine), max_attempts=2, ventana_minutos=60
    )

    await guard.register_failure("CC-999")
    await guard.register_failure("CC-999")
    with pytest.raises(DianLoginAttemptsExhaustedError):
        await guard.assert_can_attempt("CC-999")
    await engine.dispose()


async def test_reset_manual_libera_de_una(guard):
    """El botón de desbloquear: quien tecleó mal y ya sabe la clave no espera la ventana."""
    await guard.register_failure("CC-555")
    await guard.register_failure("CC-555")
    with pytest.raises(DianLoginAttemptsExhaustedError):
        await guard.assert_can_attempt("CC-555")

    await guard.reset("CC-555")
    await guard.assert_can_attempt("CC-555")  # ya no lanza
