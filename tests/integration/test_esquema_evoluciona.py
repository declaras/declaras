"""Agregar una columna al modelo no puede romper una base que ya tiene datos.

═══ EL MODO DE FALLA QUE ESTO CIERRA ═══

`create_all` crea las tablas que no existen y NO TOCA las que sí. Alcanza mientras el esquema
no cambia, y deja de alcanzar en el momento exacto en que alguien agrega una columna a una
tabla con datos: el modelo la declara, la base no la tiene, y toda consulta sobre esa tabla
empieza a fallar en producción.

Pasó de verdad al guardar la clave del portal, y el síntoma no se parecía a la causa: la lista
de clientes quedó diciendo "7 declaraciones de 0 clientes" —el conteo salía de otra tabla que
sí respondía— con la lista vacía debajo. Nada mencionaba una columna.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from declaras.adapters.persistence.engine import create_schema


@pytest.fixture
async def base_vieja(tmp_path):
    """Una base creada SIN las columnas de la clave, como la de producción antes del cambio."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/vieja.db")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE clients ("
            " id VARCHAR(36) PRIMARY KEY,"
            " id_kind VARCHAR(8),"
            " id_number VARCHAR(20),"
            " full_name VARCHAR(200),"
            " phone_number VARCHAR(30),"
            " email VARCHAR(200),"
            " created_at TIMESTAMP,"
            " updated_at TIMESTAMP)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO clients (id, id_kind, id_number, full_name)"
            " VALUES ('c1', 'CC', '1020304050', 'Persona Existente')"
        )
    yield engine
    await engine.dispose()


async def test_las_columnas_nuevas_se_agregan_a_una_tabla_que_ya_existe(base_vieja):
    async with base_vieja.connect() as conn:
        antes = await conn.run_sync(
            lambda c: {x["name"] for x in inspect(c).get_columns("clients")}
        )
    assert "dian_password_cifrada" not in antes, "la base de partida no tenia la columna"

    await create_schema(base_vieja)

    async with base_vieja.connect() as conn:
        despues = await conn.run_sync(
            lambda c: {x["name"] for x in inspect(c).get_columns("clients")}
        )
    assert "dian_password_cifrada" in despues
    assert "dian_password_guardada_at" in despues


async def test_los_datos_que_ya_estaban_siguen_ahi(base_vieja):
    """Una migracion que pierde datos es peor que la falla que arregla."""
    await create_schema(base_vieja)

    async with base_vieja.connect() as conn:
        filas = (await conn.execute(text("SELECT full_name FROM clients"))).all()
    assert [f[0] for f in filas] == ["Persona Existente"]


async def test_correrlo_dos_veces_no_falla(base_vieja):
    """Arranca en cada despliegue: si no fuera idempotente, el segundo arranque se caeria."""
    await create_schema(base_vieja)
    await create_schema(base_vieja)

    async with base_vieja.connect() as conn:
        columnas = await conn.run_sync(
            lambda c: [x["name"] for x in inspect(c).get_columns("clients")]
        )
    assert columnas.count("dian_password_cifrada") == 1


def test_el_sql_tambien_es_valido_en_el_motor_de_produccion():
    """LAS PRUEBAS CORREN EN SQLITE Y PRODUCCION ES POSTGRES, y esa diferencia ya mordió una
    vez: un cambio de esquema que pasa acá puede fallar allá.

    El tipo de cada columna se compila con el dialecto del motor, así que basta con verificar
    que los dos dialectos producen algo compilable. Un tipo que no se puede expresar en
    Postgres reventaría el ARRANQUE del despliegue, o sea el peor momento posible.
    """
    from sqlalchemy.dialects import postgresql, sqlite

    from declaras.adapters.persistence.tables import Base

    for dialecto in (postgresql.dialect(), sqlite.dialect()):
        for tabla in Base.metadata.sorted_tables:
            for columna in tabla.columns:
                compilado = columna.type.compile(dialecto)
                assert compilado, f"{tabla.name}.{columna.name} no se pudo compilar"
