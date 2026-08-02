"""Verificar un token de Supabase y decir a quien pertenece.

═══ LAS DOS PREGUNTAS, QUE SON DISTINTAS ═══

1. AUTENTICACION — este token es legitimo? Firma valida con la llave publica del proyecto,
   sin vencer, del emisor correcto, para esta audiencia.
2. AUTORIZACION — esta persona puede entrar? Su correo esta en la lista de contadores.

La numero 1 la responde Supabase con criptografia. La numero 2 NO LA RESPONDE NADIE MAS QUE
NOSOTROS, y confundirlas es el error que hay que evitar:

    Un JWT valido de Supabase prueba que alguien tiene una cuenta en ese proyecto.
    No prueba que pueda ver declaraciones de renta de otras personas.

Con el registro publico encendido —que es el default de Supabase— cualquiera crea una cuenta y su
token pasa la pregunta 1 sin una sola falla, porque la firma ES legitima. Por eso la pregunta 2
existe aca y no se delega.

═══ LO QUE SE VERIFICA Y POR QUE CADA COSA ═══

`exp`   sin esto un token robado sirve para siempre.
`iss`   sin esto sirve un token de OTRO proyecto de Supabase — y cualquiera puede crear uno
        gratis en dos minutos, con su propia llave, y firmarse lo que quiera.
`aud`   Supabase emite `authenticated` para sesiones de persona. Un token con otra audiencia
        (`anon`, o uno de servicio) no es una sesion y no debe pasar por una.
`sub`   se exige presente porque es el identificador que va a la bitacora. Un rastro sin actor
        no es un rastro.

Se pasa `algorithms=["ES256"]` explicito. Aceptar lo que diga el propio token en su cabecera es
la vulnerabilidad clasica de JWT: se manda `alg: none`, o se cambia a HMAC usando la llave
publica —que es publica— como secreto, y la firma "verifica".
"""

from __future__ import annotations

from typing import Any

import jwt

from declaras.api.auth.jwks import CacheDeLlaves, LlaveDesconocidaError
from declaras.api.auth.principal import Principal, TipoDePrincipal
from declaras.domain.errors import DeclarasError

AUDIENCIA = "authenticated"
ALGORITMOS = ["ES256"]


class TokenInvalidoError(DeclarasError):
    code = "TOKEN_INVALIDO"
    http_status = 401
    default_message = "La sesión no es válida o ya venció. Vuelve a entrar."


class NoAutorizadoError(DeclarasError):
    """El token es legitimo pero la persona no esta habilitada.

    Es 403 y no 401 a proposito: 401 significa "identificate" e invita a reintentar, 403
    significa "ya se quien eres y no". Devolver 401 aca haria que la consola mandara a alguien a
    loguearse de nuevo en un bucle que nunca puede resolverse.
    """

    code = "NO_AUTORIZADO"
    http_status = 403
    default_message = "Tu cuenta no tiene acceso a esta consola."


def emisor_de(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/auth/v1"


def jwks_url_de(supabase_url: str) -> str:
    return f"{emisor_de(supabase_url)}/.well-known/jwks.json"


async def principal_del_token(
    token: str,
    *,
    cache: CacheDeLlaves,
    emisor: str,
    contadores: list[str],
) -> Principal:
    """Verifica el token y devuelve quien es, o levanta.

    `contadores` vacio levanta `NoAutorizadoError` para todo el mundo. Es deliberado y esta
    probado: una lista de permitidos que al quedar vacia permite a todos es la forma mas comun de
    que un control de acceso se vuelva decorativo sin que nadie lo note.
    """
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as causa:
        raise TokenInvalidoError() from causa

    if not kid:
        # Sin `kid` no se sabe con que llave verificar. Probar todas seria darle al atacante
        # varios intentos de firma por request.
        raise TokenInvalidoError()

    try:
        jwk = await cache.llave_para(str(kid))
    except LlaveDesconocidaError as causa:
        raise TokenInvalidoError() from causa

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=jwt.PyJWK(jwk).key,
            algorithms=ALGORITMOS,
            audience=AUDIENCIA,
            issuer=emisor,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as causa:
        raise TokenInvalidoError() from causa

    correo = claims.get("email")
    correo = correo.strip().lower() if isinstance(correo, str) else None

    # La comparacion es contra el correo VERIFICADO del token, no contra nada que venga en la
    # peticion. Y si el token no trae correo, no hay con que comparar: se rechaza en vez de dejar
    # pasar por falta de dato.
    if correo is None or correo not in contadores:
        raise NoAutorizadoError()

    return Principal(
        subject=str(claims["sub"]),
        tipo=TipoDePrincipal.CONTADOR,
        email=correo,
        claims=claims,
    )
