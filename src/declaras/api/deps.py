"""Dependencias de FastAPI: acceso al contenedor y autenticacion."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from declaras.api.auth.principal import Principal
from declaras.api.auth.token import emisor_de, principal_del_token
from declaras.api.container import Container
from declaras.domain.errors import DeclarasError
from declaras.services.extraction import ExtractionService

# `auto_error=False` para que la ausencia de token no sea un 401 automatico de FastAPI: el mensaje
# y el codigo los decide `require_principal`, que es contrato publico de la API —el cliente
# ramifica por `code`— y no algo que deba redactar el framework.
_bearer = HTTPBearer(auto_error=False)


class UnauthorizedError(DeclarasError):
    code = "UNAUTHORIZED"
    http_status = 401
    default_message = "Necesitas haber ingresado para usar esto."


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def require_principal(
    container: ContainerDep,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)] = None,
) -> Principal:
    """Quien esta haciendo esta peticion. Es el unico portero de la API.

    ═══ SOLO TOKENS DE PERSONA. LA LLAVE DE SERVICIO SE FUE ═══

    Hubo una `X-API-Key` compartida, y el problema no fue la llave sino a QUIEN autenticaba: a un
    servicio. Como el navegador no podia tenerla, hubo que poner un proxy que la aplicara — y ese
    proxy quedo siendo un portero que le abria a cualquiera Y ADEMAS ponia la credencial de su
    bolsillo. Medido contra el despliegue publico antes de arreglarlo:

        curl https://declaras.vercel.app/api/v1/clients  ->  200, con cedulas y correos

    El rodeo era circular: se necesitaba la llave por no saber quien era el usuario, la llave no
    podia ir al navegador, hacia falta un servidor que la pusiera... y si ya hay un servidor en el
    camino, ahi mismo se autentica a la PERSONA y la llave sobra. Eso es esto.

    Lo que se gana no es solo cerrar esa puerta: la credencial ahora representa a alguien, asi que
    la bitacora puede nombrarlo, y revocar el acceso de una persona no saca a las demas.

    ═══ SIN CONFIGURACION NO ENTRA NADIE, Y ESO ES DELIBERADO ═══

    `auth_de_usuario_activo` exige proyecto de Supabase Y lista de contadores. Sin las dos no hay
    forma de autenticar a nadie y la API rechaza todo. No hay camino alterno a proposito: un
    respaldo "por si el auth no esta configurado" es exactamente la puerta que acabamos de cerrar.

    La consecuencia operativa hay que conocerla: desplegar esto sin esas variables deja la API
    inservible, no permisiva. Es el lado correcto en el que fallar.
    """
    ajustes = container.settings
    if not ajustes.auth_de_usuario_activo or container.llaves is None:
        # 503 y no 401: no es que a quien pregunta le falte una credencial —es que este despliegue
        # no puede validar ninguna. Un 401 mandaria a la consola a pedir que entre otra vez, en un
        # bucle que nadie puede resolver escribiendo bien la clave.
        raise AuthNoConfiguradoError()

    if bearer is None or not bearer.credentials:
        raise UnauthorizedError()

    return await principal_del_token(
        bearer.credentials,
        cache=container.llaves,
        emisor=emisor_de(str(ajustes.supabase_url)),
        contadores=ajustes.contadores,
    )


class AuthNoConfiguradoError(DeclarasError):
    code = "AUTH_NO_CONFIGURADO"
    http_status = 503
    default_message = "El ingreso no está configurado en este despliegue."


def get_extraction_service(container: ContainerDep) -> ExtractionService:
    return container.extraction


AutenticadoDep = Annotated[Principal, Depends(require_principal)]
ExtractionDep = Annotated[ExtractionService, Depends(get_extraction_service)]
