"""De donde viene una peticion, para poder limitarla.

═══ LA TRAMPA DEL X-FORWARDED-FOR ═══

Detras de un proxy, `request.client.host` es el proxy y no sirve para distinguir a nadie: todas
las peticiones se verian iguales. La IP real viaja en `X-Forwarded-For`, pero ese header es una
LISTA y cualquiera puede mandar el suyo:

    el cliente manda:  X-Forwarded-For: 1.2.3.4
    el proxy entrega:  X-Forwarded-For: 1.2.3.4, <ip real del cliente>

Un proxy AGREGA al final, no reemplaza. Asi que tomar el PRIMER valor —que es lo que hace casi
todo el mundo, porque "el primero es el cliente" es lo que dice la documentacion del header en
el caso sin atacante— deja el limitador inservible: basta mandar un valor distinto en cada
peticion para tener origenes infinitos.

Se toma el ULTIMO, que es el unico que escribio alguien en quien confiamos.

═══ ESTO NO ES UNA DEFENSA FUERTE, Y CONVIENE SABERLO ═══

Quien tenga muchas IP (una botnet, una nube) sigue teniendo muchos origenes. Un limite por IP
no para a un atacante decidido: para el bucle de un script y el accidente de un cliente mal
programado, que es de donde viene la mayoria del trafico raro. La defensa fuerte contra un
atacante decidido vive delante de este servicio (un WAF), no aca.
"""

from __future__ import annotations

from fastapi import Request

_SIN_ORIGEN = "desconocido"


def origen_de(request: Request) -> str:
    """La IP de quien pide, tal como la deja el proxy de confianza.

    Cuando no hay proxy (desarrollo, pruebas) se usa la conexion directa. Si no hay ninguna de
    las dos, todas las peticiones comparten un mismo cubo: es mas estricto que no limitar, y no
    limitar es lo que no puede pasar.
    """
    reenviado = request.headers.get("x-forwarded-for")
    if reenviado:
        partes = [p.strip() for p in reenviado.split(",") if p.strip()]
        if partes:
            return partes[-1]
    return request.client.host if request.client else _SIN_ORIGEN
