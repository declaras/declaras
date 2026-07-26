"""Motor y sesiones de base de datos."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from declaras.adapters.persistence.tables import Base


def create_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("sqlite"):
        db_path = database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(database_url, echo=False, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    """Crea las tablas si no existen.

    Suficiente para el piloto. Cuando el esquema empiece a evolucionar, migrar a
    Alembic antes de tener datos que no se puedan perder.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
