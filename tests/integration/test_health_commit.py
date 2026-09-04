"""La sonda de salud dice QUE commit esta corriendo.

═══ EL PROBLEMA QUE RESUELVE ═══

`version` sale del pyproject y lleva meses en 0.1.0, asi que dos despliegues distintos responden
lo mismo. Averiguar si un commit llego a produccion costo media hora de caminos indirectos —el
esquema de OpenAPI, el enum de tipos de documento, probar si un endpoint existia— y ninguno
servia, porque todos contestaban sobre cosas que ya existian antes del commit en cuestion.
"""

import os
from unittest import mock


async def test_health_dice_que_commit_esta_corriendo(client):
    cuerpo = (await client.get("/health")).json()

    assert cuerpo["commit"], "sin esto no se puede saber que codigo esta vivo"
    # No se exige un hash concreto: en local sale de git y en el despliegue de la variable del
    # proveedor. Lo que se exige es que la respuesta traiga algo con que distinguir despliegues.
    assert cuerpo["commit"] != cuerpo["version"], "el commit y la version son cosas distintas"


async def test_el_commit_del_proveedor_gana_sobre_git(client):
    """En el contenedor no hay repositorio git, asi que la variable que inyecta el proveedor es
    la unica fuente. Se recorta a siete caracteres, que es como se leen los hashes en GitHub."""
    with mock.patch.dict(os.environ, {"RAILWAY_GIT_COMMIT_SHA": "abcdef1234567890"}):
        cuerpo = (await client.get("/health")).json()

    assert cuerpo["commit"] == "abcdef1"


async def test_sin_commit_la_sonda_no_se_cae(client):
    """Una sonda de salud que falla por no saber su propio commit seria peor que no tener el
    dato: el orquestador la leeria como servicio caido y lo reiniciaria en ciclo."""
    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch("declaras.api.routers.health.subprocess.run", side_effect=OSError),
    ):
        respuesta = await client.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json()["commit"] == "desconocido"
    assert respuesta.json()["status"] == "ok"


async def test_ready_tambien_lo_trae(client):
    """`/health/ready` es la que mira el orquestador, asi que es la que mas se consulta."""
    assert (await client.get("/health/ready")).json()["commit"]
