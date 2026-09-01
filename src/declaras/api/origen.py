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

Se cuenta desde la DERECHA tantos saltos como proxies de confianza haya delante, porque esos
son los unicos valores que escribio alguien en quien confiamos. Con un proxy (el caso de
Railway) eso es el ultimo valor.

CUANTOS SALTOS HAY ES CONFIGURACION, NO UNA CONSTANTE, y esa distincion importa: si mañana
entra un CDN delante del proxy, el ultimo valor pasa a ser la IP del proxy —la misma para todo
el mundo— y el limitador empieza a contar a todos los visitantes en un solo cubo, o sea que
bloquea a gente que no hizo nada. Un numero mal puesto aca no falla: cuenta mal. Por eso se
declara en el entorno y se verifica en el primer despliegue.

═══ ESTO NO ES UNA DEFENSA FUERTE, Y CONVIENE SABERLO ═══

Quien tenga muchas IP (una botnet, una nube) sigue teniendo muchos origenes. Un limite por IP
no para a un atacante decidido: para el bucle de un script y el accidente de un cliente mal
programado, que es de donde viene la mayoria del trafico raro. La defensa fuerte contra un
atacante decidido vive delante de este servicio (un WAF), no aca.
"""

from __future__ import annotations

from fastapi import Request

_SIN_ORIGEN = "desconocido"


def origen_de(request: Request, *, saltos_de_confianza: int = 1) -> str:
    """La IP de quien pide, contando `saltos_de_confianza` valores desde la derecha.

    Cuando no hay proxy (desarrollo, pruebas) se usa la conexion directa. Si no hay ninguna de
    las dos, todas las peticiones comparten un mismo cubo: es mas estricto que no limitar, y no
    limitar es lo que no puede pasar.
    """
    reenviado = request.headers.get("x-forwarded-for")
    if reenviado:
        partes = [p.strip() for p in reenviado.split(",") if p.strip()]
        if partes:
            # Si la lista es mas corta que los saltos declarados, el cliente no mando nada y el
            # primer valor ya es el que puso el proxy mas externo.
            indice = max(0, len(partes) - max(saltos_de_confianza, 1))
            return partes[indice]
    return request.client.host if request.client else _SIN_ORIGEN
