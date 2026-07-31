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

    if "+asyncpg" in database_url:
        # SENTENCIAS PREPARADAS APAGADAS, Y NO ES UNA OPTIMIZACION AL REVES.
        #
        # asyncpg las usa por defecto, y un pooler en modo TRANSACCION —el de Supabase en el
        # puerto 6543, y PgBouncer igual— reparte cada transaccion por una conexion distinta,
        # asi que la sentencia que se preparo en una no existe en la siguiente. El sintoma es
        # un error de sentencia duplicada o inexistente que no menciona el pooler y parece un
        # bug de la aplicacion.
        #
        # Se apaga siempre y no solo cuando la URL parece de un pooler: adivinar por el host
        # falla el dia que cambie el dominio, y el costo de tenerlo apagado con esta carga
        # (108 MB de proceso, decenas de requests) es despreciable frente al de esa noche.
        #
        # Y SI HAY POOL DEL LADO DEL CLIENTE, aunque el pooler tambien agrupe. Estuvo con
        # NullPool y MEDIDO contra Supabase eso daba 11,6 segundos por request: sin pool, cada
        # operacion abre una conexion nueva, y este servicio hace varias idas a la base por
        # request. Contra Postgres local el handshake es gratis y no se ve; contra un pooler en
        # otra region es TCP + TLS + auth cada vez.
        #
        # NullPool es lo correcto en serverless, donde el proceso muere entre requests y guardar
        # conexiones no sirve de nada. Esto corre en un contenedor de vida larga, y ahi
        # reutilizarlas es justamente el punto. El pool se deja chico porque el limite de
        # conexiones del proyecto no es nuestro, y `pool_recycle` las suelta antes de que el
        # pooler las corte por su cuenta y una request se encuentre una conexion muerta.
        return create_async_engine(
            database_url,
            echo=False,
            pool_size=5,
            max_overflow=5,
            pool_recycle=300,
            pool_pre_ping=True,
            connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
        )

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
