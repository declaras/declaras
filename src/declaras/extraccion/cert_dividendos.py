"""Certificado de dividendos: el único donde no discriminar es motivo de rechazo.

Los dividendos gravados y los no gravados no pagan igual. Los no gravados entran a la cédula
de dividendos y siguen la tabla; los gravados llevan además la tarifa del artículo 240 y su
propio tratamiento del 242. Partir un total a ojo entre las dos bolsas cambia el impuesto, y
la diferencia no se ve después: las dos cifras se ven razonables y suman el total correcto.

Por eso este extractor prefiere fallar y pedir el certificado completo antes que estimar. Es
el mismo criterio del resto del proyecto —fallar ruidoso antes que declarar un número que
nadie puede sostener— aplicado al caso donde más plata se mueve por unidad de duda.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from declaras.caso import Dividendo, Fuente
from declaras.extraccion._base import (
    REGLAS_COMUNES,
    ExtraccionInvalidaError,
    extraer,
)

TOLERANCIA_RECONCILIACION_PESOS = 1_000


class MotivoDividendos(StrEnum):
    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    NO_DISCRIMINA = "no_discrimina"
    NO_RECONCILIA = "no_reconcilia"


class ExtraccionDividendosInvalidaError(ExtraccionInvalidaError[MotivoDividendos]):
    """Falla de un guard del certificado de dividendos, con su motivo etiquetado."""


PROMPT_DIVIDENDOS = f"""Este PDF es un certificado de dividendos o participaciones
(Colombia): el documento que la sociedad le entrega al socio o accionista.

Ubica cada valor por su ETIQUETA impresa, no por su posición.

- sociedad_nombre y sociedad_nit: quién distribuye. El NIT solo dígitos, sin puntos y sin
  dígito de verificación.
- anio_gravable: el año en que se PAGARON o abonaron los dividendos (el año que se declara).
- anio_utilidades: el año de las utilidades que se están distribuyendo. Suele ser anterior
  al gravable y cambia el tratamiento, así que no los confundas.
- discrimina: true SOLO si el certificado distingue explícitamente la parte gravada de la
  no gravada. Si imprime un único total sin separarlas, va false.
- gravados: dividendos GRAVADOS (los que la sociedad no había pagado impuesto sobre las
  utilidades que los originan). 0 si el certificado no los discrimina.
- no_gravados: dividendos NO GRAVADOS. 0 si el certificado no los discrimina.
- total_distribuido: el total TAL COMO lo imprime el certificado. No lo recalcules:
  cópialo. Es el testigo con el que se verifica la separación.
- retencion: retención en la fuente practicada; 0 si no hubo.

Reglas que no puedes violar:
- Si el certificado NO separa gravados de no gravados, reporta discrimina=false y deja los
  dos en 0. NUNCA estimes la separación ni pongas todo el total en una de las dos bolsas:
  las dos pagan tarifas distintas y una repartición inventada produce un impuesto
  equivocado que después no se puede detectar.
{REGLAS_COMUNES}"""


class ExtraccionDividendos(BaseModel):
    model_config = {"extra": "forbid"}

    sociedad_nit: str = Field(pattern=r"^\d{7,10}$")
    sociedad_nombre: str
    anio_gravable: int
    anio_utilidades: int
    # SIN default a propósito, igual que `pensiones_de_jubilacion` en el 220: el modelo tiene
    # que declararla siempre. Si fuera opcional, una respuesta que la omite pasaría como
    # certificado discriminado y la separación quedaría inventada en silencio.
    discrimina: bool
    gravados: int = Field(default=0, ge=0)
    no_gravados: int = Field(default=0, ge=0)
    total_distribuido: int = Field(ge=0)
    retencion: int = Field(default=0, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_dividendos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> Dividendo:
    """Extrae un certificado de dividendos y devuelve el hecho con proveniencia."""
    return extraer_dividendos_con_metadatos(pdf_bytes, anio_esperado=anio_esperado, client=client)[
        0
    ]


def extraer_dividendos_con_metadatos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[Dividendo, ExtraccionDividendos]:
    """Lo mismo, y además la extracción cruda."""
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=ExtraccionDividendos,
            prompt=PROMPT_DIVIDENDOS,
            anio_esperado=anio_esperado,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        raise ExtraccionDividendosInvalidaError(MotivoDividendos(exc.motivo), str(exc)) from exc

    if not ext.discrimina:
        # Va antes de reconciliar: sin separación los dos campos están en 0 y reconciliar
        # contra el total solo diría lo mismo con peor mensaje.
        raise ExtraccionDividendosInvalidaError(
            MotivoDividendos.NO_DISCRIMINA,
            "El certificado no discrimina la parte gravada de la no gravada, y sin esa "
            "separación no se puede liquidar: las dos pagan tarifas distintas.",
        )

    suma = ext.gravados + ext.no_gravados
    if abs(suma - ext.total_distribuido) > TOLERANCIA_RECONCILIACION_PESOS:
        raise ExtraccionDividendosInvalidaError(
            MotivoDividendos.NO_RECONCILIA,
            "La extracción no reconcilia contra el total impreso del certificado: "
            f"gravados y no gravados suman {suma:,} y el certificado dice "
            f"{ext.total_distribuido:,}.",
        )

    dividendo = Dividendo(
        sociedad_nit=ext.sociedad_nit,
        sociedad_nombre=ext.sociedad_nombre,
        gravados=ext.gravados,
        no_gravados=ext.no_gravados,
        retencion=ext.retencion,
        fuente=Fuente.documento("cert_dividendos", doc_id, confianza=ext.confianza),
    )
    return dividendo, ext
