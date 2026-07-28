"""Certificado de arrendamiento: el único cuyo guard avisa en vez de rechazar.

Los otros tres extractores fallan cuando algo no cuadra, porque la alternativa es una cifra
inventada. Acá el caso dudoso —costos por encima del canon— puede ser perfectamente cierto:
una reparación grande en un año de bajo arriendo da renta negativa de verdad. Rechazarlo
mandaría a pedir de nuevo un certificado que estaba bien, que es el error que este proyecto ya
pagó una vez con un consejo que no correspondía a la causa.

Así que acá el guard produce un aviso para el expediente y la decisión queda en una persona.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from declaras.caso import Arriendo, CostosArriendo, Fuente
from declaras.documents.models import ReadingWarning
from declaras.extraccion._base import (
    REGLAS_COMUNES,
    ExtraccionInvalidaError,
    extraer,
)


class MotivoArriendo(StrEnum):
    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    CANON_VACIO = "canon_vacio"


class ExtraccionArriendoInvalidaError(ExtraccionInvalidaError[MotivoArriendo]):
    """Falla de un guard del certificado de arrendamiento, con su motivo etiquetado."""


PROMPT_ARRIENDO = f"""Este PDF certifica ingresos por arrendamiento (Colombia). Puede venir
de una inmobiliaria, del arrendatario, o ser una relación de pagos del propietario.

Ubica cada valor por su ETIQUETA impresa, no por su posición.

- inmueble: cómo identifica el documento el inmueble arrendado (dirección, número de
  matrícula, nombre del conjunto y apartamento).
- contraparte_nombre y contraparte_nit: quién paga el canon o lo administra (el
  arrendatario o la inmobiliaria). El NIT solo dígitos, sin puntos y sin dígito de
  verificación.
- anio_gravable: el año que certifica el documento.
- canon_total: el total de cánones RECIBIDOS en el año, antes de descontar costos.
- meses: cuántos meses del año estuvo arrendado (1 a 12).
- retencion: retención en la fuente practicada sobre el arriendo; 0 si no hubo.
- predial: impuesto predial del inmueble pagado en el año.
- administracion: cuota de administración pagada en el año.
- comision_inmobiliaria: la comisión que se quedó la inmobiliaria por administrar.
- reparaciones: reparaciones y mantenimiento del inmueble pagados en el año.

Reglas que no puedes violar:
- `canon_total` es lo recibido BRUTO. Si el documento imprime un neto ya descontado, suma
  los descuentos para llegar al bruto y reporta cada descuento en su campo de costo. Un
  canon neto con los costos otra vez restados descuenta la misma plata dos veces.
- Cada costo va en SU campo y una sola vez. Si el documento trae un costo que no encaja en
  ninguno de los cuatro, ponlo en reparaciones y refleja la duda en tu confianza.
{REGLAS_COMUNES}"""


class ExtraccionArriendo(BaseModel):
    model_config = {"extra": "forbid"}

    inmueble: str
    contraparte_nombre: str
    contraparte_nit: str = Field(pattern=r"^\d{7,10}$")
    anio_gravable: int
    canon_total: int = Field(ge=0)
    meses: int = Field(default=12, ge=0, le=12)
    retencion: int = Field(default=0, ge=0)
    predial: int = Field(default=0, ge=0)
    administracion: int = Field(default=0, ge=0)
    comision_inmobiliaria: int = Field(default=0, ge=0)
    reparaciones: int = Field(default=0, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_arriendo(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> Arriendo:
    """Extrae un certificado de arrendamiento y devuelve el hecho con proveniencia."""
    return extraer_arriendo_con_metadatos(
        pdf_bytes, anio_esperado=anio_esperado, client=client
    )[0]


def extraer_arriendo_con_metadatos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[Arriendo, ReadingWarning | None, ExtraccionArriendo]:
    """Lo mismo, más el aviso de revisión si hay uno, más la extracción cruda.

    El aviso viaja aparte del hecho porque no cambia ninguna cifra: quien arma la lectura del
    documento lo pone en `warnings`, y de ahí el expediente lo convierte en un pendiente para
    el contador.
    """
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=ExtraccionArriendo,
            prompt=PROMPT_ARRIENDO,
            anio_esperado=anio_esperado,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        raise ExtraccionArriendoInvalidaError(MotivoArriendo(exc.motivo), str(exc)) from exc

    if ext.canon_total <= 0:
        # Un certificado de arriendo sin canon no certifica nada: o el modelo no encontró la
        # cifra, o el documento no es lo que se pidió. Sin canon no hay hecho que registrar.
        raise ExtraccionArriendoInvalidaError(
            MotivoArriendo.CANON_VACIO,
            "El certificado no reporta cánones recibidos en el año.",
        )

    costos = CostosArriendo(
        predial=ext.predial,
        administracion=ext.administracion,
        comision_inmobiliaria=ext.comision_inmobiliaria,
        reparaciones=ext.reparaciones,
    )
    arriendo = Arriendo(
        inmueble=ext.inmueble,
        contraparte_nombre=ext.contraparte_nombre,
        contraparte_nit=ext.contraparte_nit,
        canon_total=ext.canon_total,
        retencion=ext.retencion,
        costos=costos,
        fuente=Fuente.documento("cert_arriendo", doc_id, confianza=ext.confianza),
    )

    aviso = None
    if costos.total > ext.canon_total:
        # Puede ser cierto y puede ser un costo leído mal (o un canon neto al que le
        # volvieron a restar los descuentos). No se rechaza: se declara para que una persona
        # lo mire, porque rechazarlo mandaría a pedir de nuevo un archivo que puede estar bien.
        aviso = ReadingWarning(
            code="ARRIENDO_COSTOS_MAYORES_AL_CANON",
            message=(
                "Los costos del inmueble superan los cánones recibidos, así que la renta "
                "del arriendo queda negativa. Hay que confirmar las cifras con el "
                "propietario antes de declararlas."
            ),
        )
    return arriendo, aviso, ext
