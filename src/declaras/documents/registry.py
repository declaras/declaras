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

DONDE SI HARA FALTA MIRAR EL DOCUMENTO

En lo que manda el cliente por chat: la foto de un certificado de intereses de vivienda, de
medicina prepagada o de un aporte a AFC. Ahi no hay celdas ni operadores de texto, solo pixeles,
y cada banco usa su propio formato. Ese lector NO ESTA CONSTRUIDO todavia: un documento asi se
guarda y queda disponible para revisarlo a mano, pero su valor no entra a ningun calculo.

Agregar un documento nuevo es escribir un lector y registrarlo aqui. El tipo se recibe por
parametro, porque en el flujo del producto el agente siempre sabe que pidio. Detectar el tipo
solo (para cuando alguien manda algo que nadie le pidio) tampoco esta construido.
"""

from __future__ import annotations

from collections.abc import Callable

from declaras.documents.models import DocumentReading
from declaras.documents.parsers import einvoice_summary, exogena, renta_210, rut

Reader = Callable[[bytes], DocumentReading]

# Lectores deterministicos para los documentos que entrega el portal DIAN.
DETERMINISTIC_READERS: dict[str, Reader] = {
    "EXOGENA": exogena.parse,
    "RUT": rut.parse,
    "EINVOICE_SUMMARY": einvoice_summary.parse,
    # La declaracion presentada del anio anterior y el borrador del anio en curso son el
    # mismo formulario 210, asi que los lee el mismo parser.
    "PRIOR_RETURN": renta_210.parse,
    "SUGGESTED_RETURN": renta_210.parse,
}


def reader_for(doc_type: str) -> Reader | None:
    """Lector deterministico de una clase de documento, si existe."""
    return DETERMINISTIC_READERS.get(doc_type)


def is_deterministic(doc_type: str) -> bool:
    return doc_type in DETERMINISTIC_READERS


def supported_types() -> list[str]:
    return sorted(DETERMINISTIC_READERS)
