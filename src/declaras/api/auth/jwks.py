"""Las llaves publicas con que se verifica la firma de un token, en cache.

═══ POR QUE HAY CACHE ═══

Supabase firma con ES256, o sea llave privada suya y publica nuestra. Verificar un token exige
tener esa llave publica, que se baja de un endpoint. Sin cache eso seria UNA IDA A LA RED POR
REQUEST, en serie antes de responder: la consola hace decenas de llamadas por pantalla, asi que
serian decenas de viajes a un servicio de terceros para atender lo que ya se sabe. Y peor que la
latencia: cada request pasaria a depender de que Supabase este arriba en ese instante.

La llave cambia cada muchos meses. La cache es de una hora.

═══ POR QUE ASINCRONO Y NO `requests` ═══

Esto corre dentro de una dependencia de FastAPI, en el bucle de eventos. Una llamada HTTP
bloqueante ahi no frena solo a esa request: frena a TODAS las que el proceso esta atendiendo,
porque el bucle se queda esperando. Es el mismo error que ya se corrigio en este proyecto con el
lector de certificados, y no se repite.

═══ POR QUE EL CANDADO ═══

Al arrancar, la cache esta vacia. Si llegan diez requests juntas, las diez ven el vacio y las
diez salen a buscar la misma llave. Con el candado sale una y las otras nueve esperan y
encuentran el resultado. No es una optimizacion: es no golpear a un tercero diez veces con la
misma pregunta cada vez que el contenedor se reinicia.

═══ EL CASO QUE PARECE UN DETALLE Y NO LO ES ═══

Un `kid` que no esta en la cache puede ser que Supabase ROTO la llave. Se vuelve a bajar el
juego de llaves UNA vez antes de rechazar. Sin eso, el dia de la rotacion todos los tokens
quedan invalidos hasta que alguien reinicie el servicio — una caida total con la firma perfecta y
ningun error que la explique.

Y al reves: no se re-baja en cada fallo. Un token con un `kid` inventado no puede provocar una
ida a la red por intento, o el rechazo se vuelve el vector para golpear a Supabase desde afuera.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

VIGENCIA_CACHE_S = 3600.0
TIMEOUT_S = 10.0

# ── El cupo de bajadas por `kid` desconocido ────────────────────────────────────────────────
#
# Hay que servir dos casos que tiran para lados opuestos:
#
#   ROTACION (real, cada muchos meses): llega un `kid` nuevo y hay que bajar las llaves YA. Un
#   freno que lo haga esperar convierte la rotacion en una ventana de rechazos — y como los
#   tokens estan bien firmados, en los logs no se ve nada que la explique.
#
#   ABUSO: mandar `kid` inventados desde afuera no puede provocar una ida a la red por intento, o
#   nuestro rechazo se vuelve la forma de inundar a un tercero, gratis y sin autenticarse.
#
# Una espera fija entre bajadas atiende el segundo y ROMPE el primero: fue lo que salio en la
# prueba de rotacion. Un cupo atiende los dos. Se acumula uno cada RECARGA_S y se guarda como
# maximo CUPO_MAXIMO, asi que:
#
#   - el dia de la rotacion el cupo esta lleno (nadie mando un `kid` raro en meses) -> baja al
#     instante y no se pierde una sola request;
#   - bajo abuso el cupo se agota y el resto se rechaza sin tocar la red -> como maximo una
#     bajada cada RECARGA_S, sin importar cuantos intentos lleguen.
RECARGA_S = 10.0
CUPO_MAXIMO = 2.0


class LlaveDesconocidaError(Exception):
    """El token viene firmado con una llave que este proyecto no reconoce."""


@dataclass
class _Entrada:
    llave: dict[str, object]
    vence_en: float


class CacheDeLlaves:
    """Llaves publicas por `kid`, con vencimiento.

    Una instancia por proceso. No se hace global a nivel de modulo para que las pruebas puedan
    tener la suya y no se contaminen entre si por el orden en que corren — que es la clase de
    dependencia oculta que hace que una prueba pase sola y falle en la suite.
    """

    def __init__(self, jwks_url: str) -> None:
        self._jwks_url = jwks_url
        self._llaves: dict[str, _Entrada] = {}
        self._candado = asyncio.Lock()
        # Arranca lleno: la primera bajada del proceso no tiene que esperar a nadie.
        self._cupo = CUPO_MAXIMO
        self._cupo_visto_en = time.monotonic()

    async def llave_para(self, kid: str) -> dict[str, object]:
        if (llave := self._buscar(kid)) is not None:
            return llave

        async with self._candado:
            # Se vuelve a mirar ADENTRO del candado: mientras se esperaba, otra request pudo
            # haber bajado las llaves. Sin esta segunda mirada, las diez del arranque bajan igual
            # —una tras otra en vez de a la vez— y el candado no habria servido de nada.
            if (llave := self._buscar(kid)) is not None:
                return llave

            if not self._tomar_cupo():
                raise LlaveDesconocidaError(
                    f"El token viene firmado con una llave desconocida (kid={kid})."
                )

            await self._bajar()

        if (llave := self._buscar(kid)) is not None:
            return llave
        raise LlaveDesconocidaError(
            f"El token viene firmado con una llave que no esta en el juego de llaves (kid={kid})."
        )

    def _tomar_cupo(self) -> bool:
        """Consume un permiso de bajada si hay. Se llama con el candado tomado."""
        ahora = time.monotonic()
        self._cupo = min(CUPO_MAXIMO, self._cupo + (ahora - self._cupo_visto_en) / RECARGA_S)
        self._cupo_visto_en = ahora
        if self._cupo < 1.0:
            return False
        self._cupo -= 1.0
        return True

    def _buscar(self, kid: str) -> dict[str, object] | None:
        entrada = self._llaves.get(kid)
        if entrada is None:
            return None
        if entrada.vence_en <= time.monotonic():
            del self._llaves[kid]
            return None
        return entrada.llave

    async def _bajar(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as cliente:
            respuesta = await cliente.get(self._jwks_url)
            respuesta.raise_for_status()
            cuerpo = respuesta.json()

        vence_en = time.monotonic() + VIGENCIA_CACHE_S
        # Se reemplaza entero en vez de fusionar: una llave que Supabase saco del juego tiene que
        # dejar de valer aca tambien. Fusionar la mantendria viva hasta que venciera su hora, y
        # una llave retirada suele retirarse por algo.
        self._llaves = {
            str(llave["kid"]): _Entrada(llave=llave, vence_en=vence_en)
            for llave in cuerpo.get("keys", [])
            if llave.get("kid")
        }
