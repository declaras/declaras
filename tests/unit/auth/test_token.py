"""Que un token solo pase cuando debe pasar.

Cada prueba de acá es un ataque concreto, no un caso de uso. Un portero se prueba por lo que
RECHAZA: si solo se probara el camino feliz, un verificador que aceptara cualquier cosa pasaría
la suite entera en verde.
"""

from __future__ import annotations

import asyncio

import pytest

from declaras.api.auth.jwks import CUPO_MAXIMO, RECARGA_S
from declaras.api.auth.principal import TipoDePrincipal
from declaras.api.auth.token import (
    NoAutorizadoError,
    TokenInvalidoError,
    principal_del_token,
)
from tests.unit.auth.tokens_falsos import CORREO, EMISOR, CacheDeLlavesFalsa, EmisorFalso

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def emisor() -> EmisorFalso:
    return EmisorFalso()


@pytest.fixture
def cache(emisor: EmisorFalso) -> CacheDeLlavesFalsa:
    return CacheDeLlavesFalsa(emisor.jwks)


async def verificar(token: str, cache: CacheDeLlavesFalsa, contadores: list[str] | None = None):
    return await principal_del_token(
        token,
        cache=cache,
        emisor=EMISOR,
        contadores=[CORREO] if contadores is None else contadores,
    )


# ─────────────────────────── el camino que sí pasa ───────────────────────────


async def test_un_token_legitimo_de_alguien_habilitado_entra(emisor, cache):
    principal = await verificar(emisor.token(), cache)

    assert principal.tipo is TipoDePrincipal.CONTADOR
    assert principal.email == CORREO
    assert principal.subject == "usuario-1"
    assert principal.es_persona


async def test_el_correo_se_compara_sin_importar_mayusculas(emisor, cache):
    # Supabase puede devolver el correo como lo escribió la persona al registrarse. Si la
    # comparación distinguiera mayúsculas, la misma cuenta entraría o no según cómo la tecleó ese
    # día — y el síntoma sería "a veces me deja entrar", que es imposible de diagnosticar.
    principal = await verificar(emisor.token(email="Contador@Declaras.CO"), cache)
    assert principal.email == CORREO


async def test_lo_que_va_a_la_bitacora_es_el_correo_verificado(emisor, cache):
    principal = await verificar(emisor.token(), cache)
    assert principal.para_bitacora == CORREO


# ─────────────────────────── autenticación: la firma ───────────────────────────


async def test_un_token_vencido_no_entra(emisor, cache):
    with pytest.raises(TokenInvalidoError):
        await verificar(emisor.token(vence_en=-10), cache)


async def test_un_token_de_otro_proyecto_de_supabase_no_entra(emisor, cache):
    # El ataque más barato que existe acá: cualquiera crea un proyecto gratis de Supabase en dos
    # minutos, se registra, y se firma un token con SU llave. La firma es impecable; lo único que
    # lo delata es el emisor.
    with pytest.raises(TokenInvalidoError):
        await verificar(emisor.token(emisor="https://otro.supabase.co/auth/v1"), cache)


async def test_un_token_que_no_es_de_sesion_no_entra(emisor, cache):
    # `aud` distinto de "authenticated" no es una sesión de persona: es la llave anónima o un
    # token de servicio. Ninguno representa a alguien que se autenticó.
    with pytest.raises(TokenInvalidoError):
        await verificar(emisor.token(audiencia="anon"), cache)


async def test_firmado_con_otra_llave_no_entra(emisor, cache):
    # Mismo `kid` que el legítimo, para que el verificador busque la llave correcta y la firma
    # simplemente no cuadre.
    impostor = EmisorFalso(kid=emisor.kid)
    with pytest.raises(TokenInvalidoError):
        await verificar(impostor.token(), cache)


async def test_confusion_de_algoritmo_no_entra(emisor, cache):
    with pytest.raises(TokenInvalidoError):
        await verificar(emisor.token_hmac_con_la_publica(), cache)


async def test_sin_kid_no_entra(emisor, cache):
    import jwt

    sin_kid = jwt.encode({"sub": "x"}, "cualquier-cosa", algorithm="HS256")
    with pytest.raises(TokenInvalidoError):
        await verificar(sin_kid, cache)


async def test_basura_no_entra(cache):
    for texto in ["", "no-es-un-token", "a.b.c"]:
        with pytest.raises(TokenInvalidoError):
            await verificar(texto, cache)


async def test_sin_sub_no_entra(emisor, cache):
    # Un token sin `sub` podría pasar la firma y dejar la bitácora sin actor. Se exige.
    import jwt

    token = jwt.encode(
        {"iss": EMISOR, "aud": "authenticated", "exp": 9_999_999_999, "email": CORREO},
        emisor._privada,
        algorithm="ES256",
        headers={"kid": emisor.kid},
    )
    with pytest.raises(TokenInvalidoError):
        await verificar(token, cache)


# ─────────────────────────── autorización: la lista ───────────────────────────


async def test_un_token_perfecto_de_alguien_que_no_esta_en_la_lista_no_entra(emisor, cache):
    """El caso que separa autenticar de autorizar.

    El token es impecable: firma buena, emisor correcto, sin vencer. Lo emitió el MISMO proyecto
    de Supabase. Es exactamente lo que obtiene cualquiera que se registre si el registro público
    quedó encendido — y por eso tiene que rebotar acá.
    """
    with pytest.raises(NoAutorizadoError):
        await verificar(emisor.token(email="cualquiera@gmail.com"), cache)


async def test_la_lista_vacia_no_deja_entrar_a_nadie(emisor, cache):
    # Una lista de permitidos que al quedar vacía permite a todos es la forma más común de que un
    # control de acceso se vuelva decorativo: se despliega a un entorno sin la variable y queda
    # abierto, con la suite en verde y sin un solo error en los logs.
    with pytest.raises(NoAutorizadoError):
        await verificar(emisor.token(), cache, contadores=[])


async def test_un_token_sin_correo_no_entra(emisor, cache):
    # Sin correo no hay nada contra qué comparar. Se rechaza en vez de dejar pasar por falta de
    # dato — que es como un `None` se convierte en un permiso.
    with pytest.raises(NoAutorizadoError):
        await verificar(emisor.token(email=None), cache)


async def test_el_correo_del_token_manda_y_no_uno_puesto_a_mano(emisor, cache):
    # Un claim extra con otro nombre no puede suplantar al correo verificado.
    with pytest.raises(NoAutorizadoError):
        await verificar(
            emisor.token(email="ajeno@gmail.com", claims_extra={"user_email": CORREO}),
            cache,
        )


# ─────────────────────────── la caché de llaves ───────────────────────────


async def test_la_llave_se_baja_una_sola_vez_para_muchos_tokens(emisor, cache):
    for _ in range(5):
        await verificar(emisor.token(), cache)
    assert cache.bajadas == 1


async def test_diez_requests_al_arranque_bajan_las_llaves_una_vez(emisor, cache):
    # Sin el candado, las diez ven la caché vacía y las diez salen a la red con la misma pregunta.
    await asyncio.gather(*(verificar(emisor.token(), cache) for _ in range(10)))
    assert cache.bajadas == 1


async def test_un_kid_desconocido_hace_rebajar_las_llaves_una_vez(emisor, cache):
    """El día de la rotación.

    Supabase cambia su llave; los tokens nuevos traen un `kid` que no está en la caché. Sin la
    re-bajada, TODOS los tokens quedarían inválidos hasta que alguien reinicie el servicio: una
    caída total, con firmas perfectas y ningún error que la explique.
    """
    await verificar(emisor.token(), cache)
    assert cache.bajadas == 1

    rotado = EmisorFalso(kid="llave-2")
    cache.jwks = rotado.jwks  # Supabase publicó la nueva

    principal = await verificar(rotado.token(), cache)
    assert principal.email == CORREO
    assert cache.bajadas == 2


async def test_un_kid_inventado_no_puede_disparar_una_bajada_por_intento(emisor, cache):
    """El freno que evita que el rechazo sea el ataque.

    Sin cupo, mandar `kid` al azar desde afuera obligaría al backend a golpear a Supabase una vez
    por intento — o sea, usar nuestro rechazo para inundar a un tercero, gratis y sin
    autenticarse.

    Lo que se afirma es la propiedad que importa: las bajadas están acotadas por el CUPO y **no
    crecen con la cantidad de intentos**. Doscientos intentos cuestan lo mismo que dos.
    """
    for i in range(200):
        with pytest.raises(TokenInvalidoError):
            await verificar(emisor.token(kid=f"inventado-{i}"), cache)

    assert cache.bajadas <= CUPO_MAXIMO


async def test_la_rotacion_se_recupera_aunque_haya_habido_abuso(emisor, cache):
    """Que el freno no se vuelva el problema.

    Un atacante puede agotar el cupo a propósito. Si eso dejara la rotación bloqueada para
    siempre, el freno sería un modo de tumbar el login desde afuera. Se recupera con el tiempo:
    el cupo se recarga y la llave nueva entra.
    """
    for i in range(50):
        with pytest.raises(TokenInvalidoError):
            await verificar(emisor.token(kid=f"inventado-{i}"), cache)

    rotado = EmisorFalso(kid="llave-2")
    cache.jwks = rotado.jwks
    # El cupo sigue agotado: la llave nueva todavía no puede bajarse.
    with pytest.raises(TokenInvalidoError):
        await verificar(rotado.token(), cache)

    # Pasa el tiempo de recarga (se simula moviendo el reloj del cupo hacia atrás, que es lo mismo
    # que esperar, sin dormir en la prueba).
    cache._cupo_visto_en -= RECARGA_S * CUPO_MAXIMO

    principal = await verificar(rotado.token(), cache)
    assert principal.email == CORREO
