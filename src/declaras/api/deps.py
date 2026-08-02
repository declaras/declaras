"""Dependencias de FastAPI: acceso al contenedor y autenticacion."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from declaras.api.auth.principal import Principal, TipoDePrincipal
from declaras.api.auth.token import emisor_de, principal_del_token
from declaras.api.container import Container
from declaras.domain.errors import DeclarasError
from declaras.services.extraction import ExtractionService

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
# `auto_error=False` para que la ausencia de token no sea un 401 automatico de FastAPI: la
# decision de que falta y con que mensaje la toma `require_principal`, que es quien conoce los
# dos caminos. Con `auto_error=True` una peticion con llave y sin token moriria antes de llegar.
_bearer = HTTPBearer(auto_error=False)


class UnauthorizedError(DeclarasError):
    code = "UNAUTHORIZED"
    http_status = 401
    default_message = "Falta la llave de API o no es válida."


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def require_api_key(
    container: ContainerDep,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
) -> str:
    if not api_key or api_key not in container.settings.api_keys:
        raise UnauthorizedError()
    return api_key


async def require_principal(
    container: ContainerDep,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)] = None,
) -> Principal:
    """Quien esta haciendo esta peticion. Es el unico portero de la API.

    ═══ POR QUE ACEPTA LAS DOS COSAS, Y POR QUE ESO ES TEMPORAL ═══

    Un token de persona (Supabase) o una llave de servicio. Las dos juntas porque la migracion es
    por etapas y cada etapa tiene que quedar desplegable: hoy el front todavia habla por el proxy
    con la llave, y las pruebas y los scripts entran asi. Si esto exigiera token desde el primer
    commit, el despliegue quedaria roto hasta que el front terminara de cambiar.

    El token va PRIMERO. Con las dos credenciales presentes gana la persona: es mas especifica
    —dice quien— y dejar que la llave le gane produciria un rastro que dice "servicio" cuando
    habia alguien identificado del otro lado.

    ═══ EL DIA QUE ESTO SE SIMPLIFIQUE ═══

    Cuando el front hable directo con `Authorization: Bearer`, la rama de la llave se borra y con
    ella el proxy de Vercel y el middleware. Lo que queda es esta funcion sin el segundo camino.
    """
    if bearer is not None and bearer.credentials:
        ajustes = container.settings
        # `auth_de_usuario_activo` exige proyecto Y lista. Sin las dos, un token no se puede
        # evaluar: con proyecto y sin lista, todo token valido entraria. Se rechaza en vez de
        # caerse a la llave, porque quien mando un token espera que se juzgue el token.
        if not ajustes.auth_de_usuario_activo or container.llaves is None:
            raise UnauthorizedError("El acceso con cuenta de usuario no está habilitado.")
        return await principal_del_token(
            bearer.credentials,
            cache=container.llaves,
            emisor=emisor_de(str(ajustes.supabase_url)),
            contadores=ajustes.contadores,
        )

    if api_key and api_key in container.settings.api_keys:
        # El subject es un prefijo de la llave, NO la llave. Va a la bitacora y a los logs, y una
        # credencial completa en un rastro que se guarda y se exporta es una credencial filtrada.
        # Ocho caracteres alcanzan para distinguir cual de varias llaves se uso.
        return Principal(
            subject=api_key[:8],
            tipo=TipoDePrincipal.SERVICIO,
        )

    raise UnauthorizedError()


def get_extraction_service(container: ContainerDep) -> ExtractionService:
    return container.extraction


ApiKeyDep = Annotated[str, Depends(require_api_key)]
AutenticadoDep = Annotated[Principal, Depends(require_principal)]
ExtractionDep = Annotated[ExtractionService, Depends(get_extraction_service)]
