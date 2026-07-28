"""Certificado de pensión: el único ingreso cuya exención se calcula MES A MES.

Por eso este extractor insiste tanto en las doce mesadas y no se conforma con el total anual:
el artículo 206 numeral 5 exime hasta 1.000 UVT **por mes**, así que un año de mesadas parejas
y un año con la misma plata concentrada en dos meses NO pagan lo mismo. Repartir el total
entre doce «para tener los doce valores» es el error que este archivo existe para impedir, y
es invisible después: las doce cifras se ven razonables y el impuesto está mal.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from declaras.caso import Fuente, IngresoPension
from declaras.extraccion._base import (
    REGLAS_COMUNES,
    ExtraccionInvalidaError,
    extraer,
)

MESES_DEL_ANIO = 12

# Igual que en el 220: cubre el redondeo del propio certificado, no un campo mal leído.
TOLERANCIA_RECONCILIACION_PESOS = 1_000


class MotivoPension(StrEnum):
    """Por qué se rechazó la extracción de un certificado de pensión.

    Las tres primeras repiten los valores de `MotivoExtraccion` para que la frontera despache
    su pista sin conocer dos enumeraciones, igual que hace el 220.
    """

    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    MESADAS_INCOMPLETAS = "mesadas_incompletas"
    NO_RECONCILIA = "no_reconcilia"


class ExtraccionPensionInvalidaError(ExtraccionInvalidaError[MotivoPension]):
    """Falla de un guard del certificado de pensión, con su motivo etiquetado."""


PROMPT_PENSION = f"""Este PDF es un certificado de pensión (Colombia): el documento anual
que la administradora —Colpensiones, un fondo privado, una caja— le entrega al pensionado.

Ubica cada valor por su ETIQUETA impresa, no por su posición: el formato cambia entre
administradoras.

- pagador_nombre y pagador_nit: quién paga la pensión. El NIT solo dígitos, sin puntos y
  sin dígito de verificación.
- anio_gravable: el año que certifica el documento.
- mesadas: la lista de los DOCE pagos mensuales, de enero a diciembre, en ese orden.
  Esto es lo más importante del certificado:
  * Un pago que no sea mensual (mesada 13, 14, retroactivo, reajuste) va sumado al mes
    en que EFECTIVAMENTE se pagó, no repartido entre los doce.
  * Si un mes no tuvo pago, va 0. Nunca omitas un mes: la lista siempre trae doce valores.
  * Si el certificado solo imprime el total anual y no el detalle mensual, NO lo dividas
    entre doce: deja los doce en 0 y refleja eso en tu confianza. Un total repartido a ojo
    produce un impuesto equivocado, porque la exención de esta renta es mensual.
- total_pagado: el total anual TAL COMO lo imprime el certificado. No lo recalcules:
  cópialo. Es el testigo con el que se verifica la lista.
- retencion: retención en la fuente practicada en el año; 0 si no hubo.

Reglas que no puedes violar:
- Las mesadas son pagos de pensión. Si el documento también trae salarios u otros pagos
  laborales, no los incluyas acá.
{REGLAS_COMUNES}"""


class ExtraccionPension(BaseModel):
    model_config = {"extra": "forbid"}

    pagador_nit: str = Field(pattern=r"^\d{7,10}$")
    pagador_nombre: str
    anio_gravable: int
    mesadas: list[int]
    total_pagado: int = Field(ge=0)
    retencion: int = Field(default=0, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_pension(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> IngresoPension:
    """Extrae un certificado de pensión y devuelve el hecho con proveniencia."""
    return extraer_pension_con_metadatos(
        pdf_bytes, anio_esperado=anio_esperado, client=client
    )[0]


def extraer_pension_con_metadatos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[IngresoPension, ExtraccionPension]:
    """Lo mismo, y además la extracción cruda.

    `IngresoPension` solo lleva el hecho; el año gravable y el total impreso son metadatos de
    la extracción y quien arma la lectura del documento los necesita.
    """
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=ExtraccionPension,
            prompt=PROMPT_PENSION,
            anio_esperado=anio_esperado,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        # Reetiquetado al vocabulario de este certificado. OJO con el orden: atrapar
        # `ValueError` acá se comería la validación del esquema de pydantic —que también es
        # `ValueError`— y la marcaría con un motivo que no le corresponde.
        raise ExtraccionPensionInvalidaError(MotivoPension(exc.motivo), str(exc)) from exc

    if len(ext.mesadas) != MESES_DEL_ANIO:
        # Va antes de reconciliar: con once mesadas la suma no significa nada, y el consejo
        # que hay que dar es otro (falta un mes, no «revísalo a mano»).
        raise ExtraccionPensionInvalidaError(
            MotivoPension.MESADAS_INCOMPLETAS,
            f"El certificado no produjo doce mesadas (llegaron {len(ext.mesadas)}); "
            "la exención pensional se calcula mes a mes y necesita los doce valores.",
        )

    suma = sum(ext.mesadas)
    if abs(suma - ext.total_pagado) > TOLERANCIA_RECONCILIACION_PESOS:
        # El total impreso es el testigo independiente. Si las doce mesadas no lo reproducen,
        # el modelo se saltó un mes o contó uno dos veces — y el error se reparte por meses,
        # que es justo la dimensión de la que depende la exención.
        raise ExtraccionPensionInvalidaError(
            MotivoPension.NO_RECONCILIA,
            "La extracción no reconcilia contra el total impreso del certificado: "
            f"las mesadas suman {suma:,} y el certificado dice {ext.total_pagado:,}.",
        )

    pension = IngresoPension(
        pagador=ext.pagador_nombre,
        pagador_nit=ext.pagador_nit,
        mesadas=list(ext.mesadas),
        retencion=ext.retencion,
        fuente=Fuente.documento("cert_pension", doc_id, confianza=ext.confianza),
    )
    return pension, ext
