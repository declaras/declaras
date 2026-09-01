"""La consulta publica de "¿me toca declarar?".

ES LA UNICA RUTA SIN SESION DEL API, y es deliberado: quien pregunta si le toca declarar
todavia no es cliente y no tiene con que autenticarse. Por eso pide lo minimo, no devuelve
nada de nadie, y no abre expediente.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from declaras.api.deps import ContainerDep
from declaras.api.origen import origen_de

router = APIRouter(prefix="/v1", tags=["consultas"])

# ═══ CUANTAS CONSULTAS POR HORA DESDE UN MISMO PUNTO DE ACCESO ═══
#
# Los dos numeros son muy distintos porque las dos operaciones cuestan muy distinto. Registrar
# una consulta escribe una fila; consultar la DIAN abre una sesion en el portal y descarga un
# archivo, o sea que gasta tiempo nuestro Y credito nuestro con la DIAN.
#
# Estan puestos donde el uso legitimo no llega: quince consultas al portal en una hora desde la
# misma IP no es una persona averiguando si le toca declarar. Se cuenta por IP, asi que una
# oficina entera comparte cuota — y aun asi quince alcanza para una oficina entera.
_LIMITE_REGISTRO = 40
_LIMITE_DIAN = 15

Telefono = Annotated[str, StringConstraints(min_length=7, max_length=20)]
# Un patron propio en vez de `EmailStr`, que arrastra la dependencia `email-validator` entera
# para esto. La validacion de verdad no es sintactica: es que el correo exista, y eso solo lo
# dice mandarle algo.
Correo = Annotated[
    str, StringConstraints(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
]


class ConsultaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=2, max_length=200)
    correo: Correo
    whatsapp: Telefono
    via: str = Field(pattern="^(preguntas|dian|experto)$")
    # Lo que contesto a cada tope: {"ingresos": "si"}. Vacio para las vias que no preguntan.
    respuestas: dict[str, str] = Field(default_factory=dict)
    id_number: str | None = Field(default=None, max_length=20)
    # `SecretStr` para que no se imprima sola en un log o en una traza de error.
    dian_password: SecretStr | None = None


class ConsultaResponse(BaseModel):
    consulta_id: str
    # OBLIGADO, NO_OBLIGADO, NO_CONCLUYENTE, o None cuando la via no concluye sola.
    resultado: str | None


@router.post(
    "/consultas",
    response_model=ConsultaResponse,
    summary="Registra una consulta de obligación de declarar y devuelve el veredicto",
)
async def registrar_consulta(
    payload: ConsultaRequest, request: Request, container: ContainerDep
) -> ConsultaResponse:
    """El veredicto lo calcula el SERVIDOR sobre las respuestas, no lo recibe hecho.

    Recibirlo dejaria la regla del art. 592 en dos sitios —el navegador y el motor— y bastaria
    con abrir las herramientas del navegador para guardarse un "no obligado" que nadie calculo.
    """
    await container.limitador.registrar(
        origen=origen_de(request, saltos_de_confianza=container.settings.proxies_de_confianza),
        recurso="consultas",
        limite=_LIMITE_REGISTRO,
    )
    consulta_id, resultado = await container.consultas.registrar(
        nombre=payload.nombre,
        correo=payload.correo,
        whatsapp=payload.whatsapp,
        via=payload.via,
        respuestas=payload.respuestas,
        id_number=payload.id_number,
        dian_password=(payload.dian_password.get_secret_value() if payload.dian_password else None),
    )
    return ConsultaResponse(consulta_id=str(consulta_id), resultado=resultado)


class ConsultaDianRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=2, max_length=200)
    correo: Correo
    whatsapp: Telefono
    id_number: str = Field(min_length=5, max_length=20, pattern=r"^\d+$")
    dian_password: SecretStr
    tax_year: int = Field(ge=2015, le=2100)


@router.post(
    "/consultas/dian",
    summary="Consulta con la DIAN si la persona debe declarar, con cifras reales",
)
async def consultar_con_la_dian(
    payload: ConsultaDianRequest, request: Request, container: ContainerDep
) -> dict[str, object]:
    """Baja SOLO la exógena y compara sus cinco topes contra el límite legal del año.

    No abre expediente: quien pregunta si le toca declarar todavía no es cliente, y bajarle los
    cinco documentos seria cobrarle una extraccion completa a alguien que solo pregunto.
    """
    # ANTES de abrir sesion: el limite existe para que este servicio no golpee el portal en
    # bucle, asi que comprobarlo despues del login no serviria de nada.
    await container.limitador.registrar(
        origen=origen_de(request, saltos_de_confianza=container.settings.proxies_de_confianza),
        recurso="consultas_dian",
        limite=_LIMITE_DIAN,
    )
    return await container.consultas.consultar_con_la_dian(
        nombre=payload.nombre,
        correo=payload.correo,
        whatsapp=payload.whatsapp,
        id_number=payload.id_number,
        dian_password=payload.dian_password.get_secret_value(),
        tax_year=payload.tax_year,
    )
