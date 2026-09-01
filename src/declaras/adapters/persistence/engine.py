"""Motor y sesiones de base de datos."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from declaras.adapters.persistence.tables import Base
from declaras.observability import get_logger

log = get_logger(__name__)


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
    """Crea las tablas que faltan y agrega las columnas nuevas a las que ya existen.

    ═══ POR QUE NO ALCANZA CON `create_all` ═══

    `create_all` crea tablas que no existen y NO TOCA las que sí. Eso alcanza mientras el
    esquema no cambia, y deja de alcanzar en el momento exacto en que alguien agrega una
    columna a una tabla con datos: el modelo la declara, la base no la tiene, y toda consulta
    sobre esa tabla empieza a fallar en produccion.

    Paso de verdad, y el sintoma no se parecia a la causa: la lista de clientes quedo diciendo
    "7 declaraciones de 0 clientes" —el conteo salia de otra tabla que si respondia— con la
    lista vacia debajo. Nada mencionaba una columna.

    ═══ QUE HACE Y QUE NO ═══

    Solo agrega columnas NUEVAS y NULLABLE, que es la unica alteracion que no puede perder
    datos ni bloquear una tabla grande. No renombra, no borra, no cambia tipos: eso necesita
    decidir que pasa con lo que ya esta escrito, y esa decision no la puede tomar un arranque.

    Es un puente, no un sistema de migraciones. El dia que haya que cambiar un tipo o mover
    datos entre columnas, toca Alembic. Pero hasta entonces esto evita el modo de falla que ya
    ocurrio, que es peor que la deuda de no tener migraciones.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_agregar_columnas_faltantes)


def _agregar_columnas_faltantes(conn: Any) -> None:
    """Compara el modelo con la base y agrega lo que falte, columna por columna."""
    inspector = inspect(conn)
    existentes = set(inspector.get_table_names())

    for tabla in Base.metadata.sorted_tables:
        if tabla.name not in existentes:
            continue  # `create_all` acaba de crearla completa
        en_la_base = {c["name"] for c in inspector.get_columns(tabla.name)}
        for columna in tabla.columns:
            if columna.name in en_la_base:
                continue
            if not columna.nullable:
                # Una columna obligatoria necesita decidir que valor llevan las filas que ya
                # estan, y eso no se decide en un arranque: se avisa y se sigue.
                log.error(
                    "schema.columna_obligatoria_faltante",
                    tabla=tabla.name,
                    columna=columna.name,
                )
                continue
            tipo = columna.type.compile(conn.dialect)
            conn.exec_driver_sql(f'ALTER TABLE {tabla.name} ADD COLUMN "{columna.name}" {tipo}')
            log.info("schema.columna_agregada", tabla=tabla.name, columna=columna.name)
