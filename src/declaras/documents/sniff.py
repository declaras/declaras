"""Adivinar qué documento es, para cuando el cliente manda algo que nadie le pidió.

El camino normal NO pasa por acá: en el flujo del producto el tipo se sabe antes de recibir el
archivo, porque la petición lo pidió. Informarlo elimina una fuente de error y esa es la razón
de que `DocumentReaderService.read` reciba el tipo por parámetro en vez de adivinarlo.

Esto existe para el otro caso: alguien manda cuatro PDF juntos sin decir qué es cada uno.
Adivinar mal es peor que no adivinar —un certificado de ICETEX leído como prepagada mete la
cifra bajo otro tope—, así que la única respuesta permitida además de un tipo conocido es
DESCONOCIDO, y quien reciba DESCONOCIDO tiene que preguntar. Nunca hay un default.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from declaras.documents.registry import supported_types
from declaras.extraccion._base import REGLAS_COMUNES, extraer
from declaras.observability import get_logger

log = get_logger(__name__)

DESCONOCIDO = "DESCONOCIDO"

# Cuánta certeza se le exige al modelo para aceptar su clasificación. Por debajo se responde
# DESCONOCIDO y alguien pregunta: el costo de preguntar es una pregunta, y el de clasificar mal
# es una cifra en el renglón equivocado del formulario.
CONFIANZA_MINIMA = 0.7

# Qué es cada tipo, en una línea, para que el modelo pueda distinguirlos. Se arma acá y no se
# saca de los prompts de cada extractor porque esto es clasificación, no extracción: lo que
# importa es en qué se DIFERENCIAN, no qué campos tiene cada uno.
_DESCRIPCIONES: dict[str, str] = {
    "EXOGENA": "reporte de información exógena de la DIAN: una tabla de terceros que "
    "reportaron pagos al contribuyente",
    "RUT": "Registro Único Tributario: la cédula tributaria, con responsabilidades y "
    "actividad económica",
    "PRIOR_RETURN": "una declaración de renta ya presentada (formulario 210)",
    "SUGGESTED_RETURN": "la declaración sugerida que la DIAN le propone al contribuyente",
    "EINVOICE_SUMMARY": "resumen de facturación electrónica",
    "CERT_INGRESOS_220": "certificado de ingresos y retenciones del EMPLEADOR "
    "(formulario 220): salarios, prestaciones y aportes obligatorios",
    "CERT_PENSION": "certificado de PENSIÓN de la administradora, con las mesadas del año",
    "CERT_BANCARIO": "certificado tributario de un BANCO: rendimientos financieros, "
    "retención y gravamen a los movimientos financieros",
    "CERT_DIVIDENDOS": "certificado de DIVIDENDOS de una sociedad a su socio",
    "CERT_ARRIENDO": "certificado de ARRENDAMIENTO: cánones recibidos por un inmueble",
    "CERT_PREPAGADA": "certificado de MEDICINA PREPAGADA o plan complementario de salud",
    "CERT_INTERESES_VIVIENDA": "certificado de INTERESES de un crédito de VIVIENDA",
    "CERT_ICETEX": "certificado de intereses de un crédito educativo del ICETEX",
    "CERT_AFC_FVP": "certificado de aportes VOLUNTARIOS a cuenta AFC o pensión voluntaria",
    "CERT_DONACION_ESAL": "certificado de DONACIÓN a una entidad sin ánimo de lucro",
}


class _Clasificacion(BaseModel):
    model_config = {"extra": "forbid"}

    doc_type: str
    confianza: float = Field(ge=0.0, le=1.0)


def _prompt(tipos: list[str]) -> str:
    lineas = "\n".join(f"- {t}: {_DESCRIPCIONES[t]}" for t in tipos if t in _DESCRIPCIONES)
    return f"""Clasifica este PDF en uno de los siguientes tipos de documento, para una
declaración de renta colombiana:

{lineas}
- {DESCONOCIDO}: cualquier otra cosa, o un documento que no puedas identificar con certeza.

Extrae:
- doc_type: exactamente una de las etiquetas de arriba, en mayúsculas.
- confianza: qué tan seguro estás de la clasificación, 0.0-1.0.

Reglas que no puedes violar:
- Si dudas entre dos tipos, responde {DESCONOCIDO}. Clasificar mal un certificado mete su
  cifra en el renglón equivocado del formulario, y preguntar cuesta solo una pregunta.
- No inventes etiquetas: solo las de la lista.
{REGLAS_COMUNES}"""


def detectar_tipo(pdf_bytes: bytes, client: Any = None) -> str:
    """Clasifica un PDF y devuelve un `doc_type` del registry, o `DESCONOCIDO`.

    Una sola llamada. Nunca levanta por un documento que no reconoce: no reconocer es una
    respuesta válida, y el error que sí importa —un archivo que no es PDF— ya lo ataja la base.
    """
    tipos = sorted(supported_types())
    ext, _ = extraer(
        pdf_bytes,
        schema=_Clasificacion,
        prompt=_prompt(tipos),
        # No hay año que verificar: clasificar no lee cifras.
        anio_esperado=None,
        client=client,
    )
    if ext.doc_type not in tipos:
        # Incluye el caso en que el modelo respondió DESCONOCIDO y el caso en que se inventó
        # una etiqueta: los dos significan lo mismo para quien llama, que tiene que preguntar.
        log.info("documents.sniff.desconocido", respondido=ext.doc_type)
        return DESCONOCIDO
    if ext.confianza < CONFIANZA_MINIMA:
        log.info("documents.sniff.confianza_baja", respondido=ext.doc_type, confianza=ext.confianza)
        return DESCONOCIDO
    return ext.doc_type
