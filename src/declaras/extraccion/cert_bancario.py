"""Certificado bancario: el único que trae un ingreso y un beneficio en el mismo papel.

Los rendimientos financieros son renta de capital; el gravamen a los movimientos financieros
—el 4x1000— es una deducción. Vienen impresos juntos, a veces en renglones contiguos, y
confundirlos cuesta doble: inventa ingreso y pierde la deducción a la vez. Por eso este
extractor devuelve dos cosas y no una.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from declaras.caso import Fuente, MontoDeclarado, Rendimiento
from declaras.extraccion._base import (
    REGLAS_COMUNES,
    ExtraccionInvalidaError,
    extraer,
)

# Cuánta confianza se le descuenta a una cifra que agrega varias cuentas. No es un castigo:
# es que nadie puede verificarla cuenta por cuenta contra el certificado.
DESCUENTO_POR_AGREGAR_CUENTAS = 0.1


class MotivoBancario(StrEnum):
    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    SIN_CUENTAS = "sin_cuentas"


class ExtraccionBancarioInvalidaError(ExtraccionInvalidaError[MotivoBancario]):
    """Falla de un guard del certificado bancario, con su motivo etiquetado."""


PROMPT_BANCARIO = f"""Este PDF es un certificado tributario bancario (Colombia): el
documento anual que el banco le entrega al cliente para su declaración de renta.

Ubica cada valor por su ETIQUETA impresa, no por su posición: cada banco arma el suyo
distinto.

- entidad_nombre y entidad_nit: el banco. El NIT solo dígitos, sin puntos y sin dígito
  de verificación.
- anio_gravable: el año que certifica el documento.
- rendimientos: los rendimientos financieros o intereses ABONADOS en el año (lo que el
  banco pagó al cliente). No incluyas el saldo ni los aportes del cliente.
- retencion: retención en la fuente practicada sobre esos rendimientos; 0 si no hubo.
- gmf_pagado: el Gravamen a los Movimientos Financieros pagado en el año, también llamado
  "4x1000" o "GMF". Es un IMPUESTO que pagó el cliente, NO un rendimiento: nunca lo sumes
  a `rendimientos`. 0 si el certificado no lo reporta.
- saldo_31_dic: el saldo de las cuentas al 31 de diciembre; 0 si no aparece.
- numero_de_cuentas: cuántas cuentas, CDT o productos distintos ampara el certificado.
  Si el documento certifica varios productos, suma sus rendimientos y sus retenciones y
  reporta acá cuántos eran.

Reglas que no puedes violar:
- `rendimientos` y `gmf_pagado` son cosas distintas y nunca se mezclan: el primero es
  ingreso del cliente, el segundo es un impuesto que el cliente pagó.
{REGLAS_COMUNES}"""


class ExtraccionBancario(BaseModel):
    model_config = {"extra": "forbid"}

    entidad_nit: str = Field(pattern=r"^\d{7,10}$")
    entidad_nombre: str
    anio_gravable: int
    rendimientos: int = Field(ge=0)
    retencion: int = Field(default=0, ge=0)
    gmf_pagado: int = Field(default=0, ge=0)
    saldo_31_dic: int = Field(default=0, ge=0)
    numero_de_cuentas: int = Field(default=1, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_bancario(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[Rendimiento, MontoDeclarado | None]:
    """Extrae un certificado bancario: `(rendimiento, GMF pagado o None)`.

    El GMF sale aparte porque no es ingreso: entra a los beneficios del caso
    (`Beneficios.gmf_pagado`), donde la ley deja deducir la mitad.
    """
    return extraer_bancario_con_metadatos(
        pdf_bytes, anio_esperado=anio_esperado, client=client
    )[:2]


def extraer_bancario_con_metadatos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client: Any = None,
) -> tuple[Rendimiento, MontoDeclarado | None, ExtraccionBancario]:
    """Lo mismo, y además la extracción cruda."""
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=ExtraccionBancario,
            prompt=PROMPT_BANCARIO,
            anio_esperado=anio_esperado,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        raise ExtraccionBancarioInvalidaError(MotivoBancario(exc.motivo), str(exc)) from exc

    if ext.numero_de_cuentas < 1:
        # Cero cuentas y una cifra de rendimientos es una contradicción del propio
        # documento: o el certificado no ampara nada, o el modelo leyó mal el conteo.
        raise ExtraccionBancarioInvalidaError(
            MotivoBancario.SIN_CUENTAS,
            "El certificado no reporta ninguna cuenta o producto, así que no se sabe "
            "de dónde salen las cifras.",
        )

    confianza = ext.confianza
    if ext.numero_de_cuentas > 1:
        # La cifra agregada puede ser exacta, pero deja de ser verificable renglón por
        # renglón. Que la confianza lo diga es lo que después ordena la cola del contador.
        confianza = max(0.0, confianza - DESCUENTO_POR_AGREGAR_CUENTAS)

    fuente = Fuente.documento("cert_bancario", doc_id, confianza=confianza)
    rendimiento = Rendimiento(
        entidad=ext.entidad_nombre,
        entidad_nit=ext.entidad_nit,
        valor=ext.rendimientos,
        retencion=ext.retencion,
        fuente=fuente,
    )
    # Sin GMF no se construye el beneficio: un `MontoDeclarado(valor=0)` diría que el
    # certificado afirma que no pagó GMF, y lo que pasa es que no lo reporta.
    gmf = MontoDeclarado(valor=ext.gmf_pagado, fuente=fuente) if ext.gmf_pagado > 0 else None
    return rendimiento, gmf, ext
