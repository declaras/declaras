"""Registro de lectores de documentos.

POR QUE AQUI NO HAY OCR, Y NO ES UN OLVIDO

OCR sirve para cuando se tienen pixeles y hace falta texto. Nada de lo que entrega el portal
de la DIAN es pixeles:

  Exogena y facturas   son XLSX. Los valores estan en celdas, ya tipados. `F15` devuelve el
                       entero 70240264, no la imagen de un numero.
  RUT y formulario 210 son PDF con capa de texto. Cada valor es un operador de dibujo con su
                       posicion: `1 0 0 1 189.97 569.47 Tm (29,702,000) Tj`. El numero ya esta
                       escrito; lo unico que hay que resolver es a que casilla pertenece.

Pasarle un modelo de vision a esos documentos seria cambiar un resultado exacto y reproducible
por uno estimado, mas lento y mas caro, para leer algo que ya viene legible. Por eso los cuatro
lectores son deterministicos y reportan `Confidence.DETERMINISTIC` cuando leen una celda.

El 210 tiene un matiz que si vale la pena entender: los NUMEROS DE CASILLA (el "32" impreso al
lado del valor) si son parte de una imagen de fondo, y esos son ilegibles sin OCR. Por eso su
lector ubica cada valor por su coordenada y no por la etiqueta que tiene al lado. Se resolvio
transcribiendo el mapa del formulario una vez, en vez de descifrar la imagen en cada lectura
(ver ADR 0008).

DONDE SI HAY QUE LEER CON UN MODELO

En lo que manda el cliente: el certificado de ingresos y retenciones (formulario 220), y mas
adelante el de intereses de vivienda, el de medicina prepagada o el de un aporte a AFC. Ahi no
hay celdas ni operadores en posiciones conocidas: cada emisor arma el documento como quiere, y
el mismo dato aparece con otra etiqueta y en otro sitio. Por eso son la segunda familia de este
registro, la de lectores con modelo, y por eso sus valores llegan con la confianza que el modelo
declaro en vez de con `Confidence.DETERMINISTIC`.

De esa familia hoy solo esta construido el 220 (`parsers/certificados.py`). Un documento sin
lector se guarda y queda disponible para revisarlo a mano, pero su valor no entra a ningun
calculo.

Agregar un documento nuevo es escribir un lector y registrarlo aqui, en la familia que le
corresponda. El tipo se recibe por parametro, porque en el flujo del producto el agente siempre
sabe que pidio. Detectar el tipo solo (para cuando alguien manda algo que nadie le pidio)
tampoco esta construido.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Protocol

from declaras.documents.models import DocumentReading
from declaras.documents.parsers import certificados, einvoice_summary, exogena, renta_210, rut

Reader = Callable[[bytes], DocumentReading]


class LlmReader(Protocol):
    """Lector con modelo: ademas del contenido recibe el contexto del caso.

    El contexto va por palabra clave y con default para que el lector se pueda usar suelto
    (un script, una prueba), pero quien despacha lecturas nunca lo omite: se lo ata `reader_for`.
    """

    def __call__(self, content: bytes, *, anio_esperado: int | None = ...) -> DocumentReading: ...


# Un lector ya atado al contexto del caso, listo para llamarse con `(content)`. Los de la
# familia con modelo necesitan saber de que anio es el caso —el error mas comun es subir el
# certificado del anio equivocado, y el guard que lo ataja no puede adivinarlo—, pero quien
# despacha una lectura no tiene por que saber cual de las dos familias es. El contexto se ata
# aqui y del otro lado sigue siendo un `Reader`.
ContextualReader = Reader

# Lectores deterministicos para los documentos que entrega el portal DIAN.
DETERMINISTIC_READERS: dict[str, Reader] = {
    "EXOGENA": exogena.parse,
    "RUT": rut.parse,
    "EINVOICE_SUMMARY": einvoice_summary.parse,
    # La declaracion presentada del anio anterior y el borrador del anio en curso son el
    # mismo formulario 210, asi que los lee el mismo parser.
    "PRIOR_RETURN": renta_210.parse,
    "SUGGESTED_RETURN": renta_210.parse,
    # El mismo formulario 210, presentado en vez de sugerido.
    "FILED_RETURN": renta_210.parse,
}

# Lectores con modelo para los certificados que aporta el cliente. Van aparte de los
# deterministicos y no mezclados con ellos porque cuestan una llamada al modelo y su
# resultado es estimado: quien despacha una lectura tiene que poder distinguirlos
# (`is_deterministic`) sin mirar el nombre del tipo.
LLM_READERS: dict[str, LlmReader] = {
    "CERT_INGRESOS_220": certificados.leer_220,
    "CERT_PENSION": certificados.leer_pension,
    "CERT_BANCARIO": certificados.leer_bancario,
    "CERT_DIVIDENDOS": certificados.leer_dividendos,
    "CERT_ARRIENDO": certificados.leer_arriendo,
    # Los cinco de beneficios comparten extractor y se registran generados: cada uno lleva su
    # `doc_type` esperado adentro, así que un certificado del beneficio equivocado falla en vez
    # de entrar bajo otro tope.
    **certificados.LECTORES_DE_BENEFICIO,
}


def reader_for(doc_type: str, *, anio_esperado: int | None = None) -> ContextualReader | None:
    """Lector de una clase de documento, de cualquiera de las dos familias, si existe.

    `anio_esperado` es el anio gravable del caso. Los parsers del portal lo ignoran (leen una
    celda, y la celda no depende del caso), asi que se devuelven tal cual; a los de la familia
    con modelo se les ata, porque para ellos es un guard: el certificado que trae otro anio
    gravable se rechaza en vez de entrar al expediente con las cifras del anio equivocado.
    """
    deterministico = DETERMINISTIC_READERS.get(doc_type)
    if deterministico is not None:
        return deterministico
    con_modelo = LLM_READERS.get(doc_type)
    if con_modelo is None:
        return None
    # Se ata siempre, tambien cuando el anio es None: asi el lector recibe explicitamente que
    # nadie sabia el anio, en vez de que la ausencia del argumento signifique dos cosas.
    return partial(con_modelo, anio_esperado=anio_esperado)


def is_deterministic(doc_type: str) -> bool:
    return doc_type in DETERMINISTIC_READERS


def supported_types() -> list[str]:
    return sorted(DETERMINISTIC_READERS | LLM_READERS)
