"""Quien esta autenticado, como un solo objeto.

═══ POR QUE UN OBJETO Y NO EL TOKEN ═══

Sin esto, cada sitio que necesita saber quien pidio algo lee un claim de un JWT, y el codigo
queda amarrado al proveedor: cambiar Supabase por otra cosa —o agregar la entrada del
contribuyente— obligaria a tocar todos esos sitios. Con esto, el resto del backend depende del
`Principal` y nunca del token; la unica pieza que sabe de JWT es la que lo construye.

═══ POR QUE ES INMUTABLE ═══

Es la respuesta a "quien eres" despues de haberla verificado. Un objeto que se puede modificar
despues permite que un tramo del codigo escale sus propios permisos sin que se note, y ese es
exactamente el bug que nadie encuentra leyendo. `frozen=True` lo hace imposible en vez de
improbable.

═══ LO QUE ESTE MODELO NO TIENE, A PROPOSITO ═══

No hay scopes ni permisos. Hoy hay un solo actor —el contador— y quien entra ve todo, que es la
verdad del negocio y no una simplificacion. Inventar un sistema de permisos para un unico rol
produce codigo que nadie ejercita y que, cuando por fin llegue el segundo actor, va a estar
calibrado para un problema que nunca existio.

Lo que SI esta previsto es de donde va a salir el segundo actor: `tipo`. El dia que entre el
contribuyente, el discrimina, y la pregunta "este expediente es tuyo?" se escribe contra el
`Principal` sin desarmar nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TipoDePrincipal(StrEnum):
    """Que clase de entidad quedo autenticada."""

    CONTADOR = "contador"
    """Persona que opera la consola. Ve todos los expedientes."""

    SERVICIO = "servicio"
    """Llamada de maquina con llave de API. Es el estado previo al auth de personas.

    Se conserva porque el front todavia habla por el proxy y porque las pruebas y los scripts
    entran asi. No es una persona: su rastro en la bitacora no puede hacerse pasar por una.
    """


@dataclass(frozen=True)
class Principal:
    """Una identidad ya verificada.

    `subject` es el identificador estable —el `sub` del token, o la etiqueta de la llave— y es lo
    que va a la bitacora. `email` puede faltar (un servicio no tiene correo), asi que quien
    escriba un rastro legible tiene que caer en `subject`, no asumir el correo.
    """

    subject: str
    tipo: TipoDePrincipal
    email: str | None = None
    claims: dict[str, object] | None = field(default=None, repr=False)

    @property
    def es_persona(self) -> bool:
        return self.tipo is not TipoDePrincipal.SERVICIO

    @property
    def para_bitacora(self) -> str:
        """Como se nombra a este principal en el rastro de auditoria.

        POR QUE IMPORTA: hoy la consola manda `quien: "contador"` en el cuerpo de la peticion, o
        sea que el navegador DECLARA quien decidio. Eso no es un rastro, es una etiqueta que el
        cliente elige. Con esto el valor sale de un token verificado, y el navegador deja de
        tener voz en el asunto.

        El servicio se marca como tal y no se disfraza de persona: una decision tomada por un
        script tiene que verse distinta de una que tomo alguien.
        """
        if self.tipo is TipoDePrincipal.SERVICIO:
            return f"servicio:{self.subject}"
        return self.email or self.subject
