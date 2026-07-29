"""Los cinco certificados de beneficios, en un solo módulo.

Van juntos y no en cinco archivos porque son el mismo documento con distinto encabezado: una
entidad, un NIT, un valor pagado en el año y el año. Lo que cambia entre prepagada, intereses
de vivienda, ICETEX, AFC/FVP y donaciones no es la forma del certificado sino el tope legal
que se le aplica después, y eso vive en el motor, no acá.

LO ÚNICO INTERESANTE DE ESTE MÓDULO ES QUE EL HINT NO SILENCIA LA DISCREPANCIA

Quien sube el archivo suele decir qué cree que es —la petición pedía prepagada, así que el
archivo debería ser de prepagada—. Ese dato ayuda al modelo, pero si el modelo lee otra cosa,
gana el documento y la extracción falla. La razón: el caso más probable no es que el modelo se
equivoque, es que la persona subió el archivo en la casilla equivocada, y aceptar el hint
metería el certificado del ICETEX en la casilla de la prepagada, donde el tope es otro.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from declaras.caso import Fuente, MontoDeclarado
from declaras.extraccion._base import (
    REGLAS_COMUNES,
    ExtraccionInvalidaError,
    extraer,
)


class TipoBeneficio(StrEnum):
    """Los cinco beneficios que se certifican con un documento de un tercero."""

    PREPAGADA = "PREPAGADA"
    INTERESES_VIVIENDA = "INTERESES_VIVIENDA"
    ICETEX = "ICETEX"
    AFC_FVP = "AFC_FVP"
    DONACION_ESAL = "DONACION_ESAL"


class MotivoBeneficio(StrEnum):
    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    TIPO_QUE_NO_COINCIDE = "tipo_que_no_coincide"
    SIN_VALOR = "sin_valor"
    SIN_CERTIFICAR = "sin_certificar"


class ExtraccionBeneficioInvalidaError(ExtraccionInvalidaError[MotivoBeneficio]):
    """Falla de un guard de un certificado de beneficio, con su motivo etiquetado."""


PROMPT_BENEFICIO = f"""Este PDF certifica un pago que da derecho a un beneficio tributario en
la declaración de renta colombiana. Puede ser de cinco clases:

- PREPAGADA: medicina prepagada o plan complementario de salud, pagado por el contribuyente
  a una aseguradora o EPS. NO son los aportes obligatorios a salud (esos van en el
  certificado del empleador).
- INTERESES_VIVIENDA: intereses pagados en el año sobre un crédito hipotecario o un leasing
  habitacional. Solo los INTERESES, no el abono a capital ni la cuota total.
- ICETEX: intereses pagados en el año sobre un crédito educativo del ICETEX.
- AFC_FVP: aportes voluntarios a una cuenta AFC (Ahorro para el Fomento de la Construcción)
  o a un fondo de pensiones voluntarias. NO son los aportes obligatorios a pensión.
- DONACION_ESAL: donación a una entidad sin ánimo de lucro del régimen tributario especial.

Extrae:
- tipo: cuál de las cinco clases es este documento, según lo que el documento DICE ser.
- entidad y entidad_nit: quién expide el certificado. El NIT solo dígitos, sin puntos y sin
  dígito de verificación.
- valor: el total PAGADO en el año que da derecho al beneficio, en pesos. Para intereses, solo
  la parte de intereses. Para AFC/FVP, solo los aportes voluntarios del contribuyente.
- anio_gravable: el año que certifica el documento.
- certificada: true si el documento es un certificado formal expedido por la entidad (con su
  nombre y NIT); false si es un extracto, una captura de pantalla, un comprobante de pago
  suelto o cualquier cosa que la DIAN no aceptaría como soporte.

Reglas que no puedes violar:
- El `tipo` sale de lo que el documento dice ser, no de lo que parezca más probable. Si el
  documento no encaja claramente en ninguna de las cinco clases, elige la más cercana y
  refleja la duda en tu confianza.
- Para intereses (vivienda, ICETEX) nunca reportes la cuota total ni el saldo del crédito:
  solo los intereses pagados en el año. Confundirlos multiplica el beneficio.
{REGLAS_COMUNES}"""


class ExtraccionBeneficio(BaseModel):
    model_config = {"extra": "forbid"}

    tipo: TipoBeneficio
    entidad: str
    entidad_nit: str = Field(pattern=r"^\d{7,10}$")
    valor: int = Field(ge=0)
    anio_gravable: int
    # SIN default: el modelo tiene que declararlo. Si fuera opcional, una captura de pantalla
    # de la banca en línea pasaría como certificado formal, y ese es el soporte que la DIAN
    # pide si pregunta.
    certificada: bool
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_beneficio(
    pdf_bytes: bytes,
    tipo: TipoBeneficio | None = None,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[TipoBeneficio, MontoDeclarado]:
    """Lee un certificado de beneficio: `(tipo leído, monto con proveniencia)`.

    `tipo` es un hint: si viene y NO coincide con lo que el modelo leyó, falla. El hint sirve
    para ayudar, no para imponerse — ver el docstring del módulo.
    """
    return extraer_beneficio_con_metadatos(
        pdf_bytes, tipo=tipo, anio_esperado=anio_esperado, client=client
    )[:2]


def extraer_beneficio_con_metadatos(
    pdf_bytes: bytes,
    tipo: TipoBeneficio | None = None,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[TipoBeneficio, MontoDeclarado, ExtraccionBeneficio]:
    """Lo mismo, y además la extracción cruda."""
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=ExtraccionBeneficio,
            prompt=PROMPT_BENEFICIO,
            anio_esperado=anio_esperado,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        raise ExtraccionBeneficioInvalidaError(MotivoBeneficio(exc.motivo), str(exc)) from exc

    if tipo is not None and ext.tipo is not tipo:
        # Gana el documento. El caso probable no es que el modelo se equivoque de clase —son
        # cinco encabezados muy distintos— sino que la persona subió el archivo en la casilla
        # equivocada, y aceptar el hint metería el certificado en la casilla de otro tope.
        raise ExtraccionBeneficioInvalidaError(
            MotivoBeneficio.TIPO_QUE_NO_COINCIDE,
            f"Se esperaba un certificado de {tipo.value} y el documento es de {ext.tipo.value}.",
        )

    if ext.valor <= 0:
        # Un certificado de beneficio en cero no da derecho a nada, y lo más probable es que
        # el modelo no encontró la cifra: registrarlo como 0 dejaría la petición cerrada con
        # el beneficio perdido, que es peor que pedirlo de nuevo.
        raise ExtraccionBeneficioInvalidaError(
            MotivoBeneficio.SIN_VALOR,
            "El certificado no reporta ningún valor pagado en el año.",
        )

    if not ext.certificada:
        # No es el documento que la DIAN pide como soporte. Se rechaza acá y no se declara con
        # una nota, porque un beneficio soportado en una captura de pantalla es el que hay que
        # devolver con intereses si la DIAN revisa dentro de los tres años de firmeza.
        raise ExtraccionBeneficioInvalidaError(
            MotivoBeneficio.SIN_CERTIFICAR,
            "El documento no es un certificado formal de la entidad, así que no sirve de "
            "soporte del beneficio.",
        )

    monto = MontoDeclarado(
        valor=ext.valor,
        fuente=Fuente.documento(f"cert_{ext.tipo.value.lower()}", doc_id, confianza=ext.confianza),
    )
    return ext.tipo, monto, ext


# El `doc_type` del registry para cada clase. Es la misma cadena con la que el catálogo de
# peticiones pide el documento: si divergen, el cliente manda el archivo correcto y su
# petición no se cierra nunca (hay un test que ata las dos tablas).
DOC_TYPE_POR_TIPO: dict[TipoBeneficio, str] = {
    TipoBeneficio.PREPAGADA: "CERT_PREPAGADA",
    TipoBeneficio.INTERESES_VIVIENDA: "CERT_INTERESES_VIVIENDA",
    TipoBeneficio.ICETEX: "CERT_ICETEX",
    TipoBeneficio.AFC_FVP: "CERT_AFC_FVP",
    TipoBeneficio.DONACION_ESAL: "CERT_DONACION_ESAL",
}

TIPO_POR_DOC_TYPE: dict[str, TipoBeneficio] = {v: k for k, v in DOC_TYPE_POR_TIPO.items()}
