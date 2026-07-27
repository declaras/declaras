"""Lector del RUT en PDF.

El RUT que entrega el portal NO tiene campos de formulario (AcroForm): es un PDF
renderizado donde cada digito de un numero ocupa su propia caja. La extraccion de texto
por posicion (x, y) no es confiable aqui, porque las corridas de texto continuas no
siempre reportan coordenadas reales.

Lo que si es confiable, verificado contra un RUT real: el generador dibuja primero **toda
la plantilla estatica** (etiquetas, secciones) como un bloque de texto enorme, y **despues,
en el mismo orden visual del formulario**, dibuja cada valor diligenciado como un fragmento
de texto aparte. Por eso el parser localiza el final de ese bloque de plantilla y lee los
valores como una secuencia ordenada, con un cursor que busca el siguiente fragmento cuya
forma coincide con lo esperado (solo digitos, solo letras, fecha de 8 digitos...), en vez
de asumir una posicion fija: asi tolera que algunos campos opcionales vengan vacios.

HONESTIDAD DEL MODELO: a diferencia del parser de exogena (que lee celdas de una hoja de
calculo y es cien por ciento cierto), este es un parser posicional sobre un PDF sin
estructura. Cada valor se reporta con confianza `Confidence.LOW` y el texto completo queda
disponible en un campo aparte para que un contador lo revise si algo no cuadra.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from io import BytesIO

from pypdf import PdfReader

from declaras.documents.models import (
    Confidence,
    DocumentReading,
    ExtractedField,
    ReadingWarning,
)
from declaras.domain.errors import DocumentUnreadableError
from declaras.observability import get_logger

log = get_logger(__name__)

PARSER_NAME = "rut.pdf.v1"

# Marca de que un fragmento es parte del bloque de plantilla estatica, no un valor.
_TEMPLATE_MARKER = "IDENTIFICACIÓN"
_MIN_TEMPLATE_FRAGMENT_LEN = 100

_NATURAL_PERSON_MARKERS = ("natural", "sucesión", "sucesion")
_ID_KIND_LABELS = (
    "Cédula de Ciudadanía",
    "Cédula de Extranjería",
    "Tarjeta de Identidad",
    "Pasaporte",
    "NIT",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DIGITS_RE = re.compile(r"\d")


class _Cursor:
    """Recorre la secuencia buscando, con una ventana acotada, el proximo fragmento que
    cumpla una condicion. Tolera fragmentos de sobra (campos opcionales presentes) sin
    perder la sincronia con los que si nos interesan."""

    def __init__(self, tokens: list[str], *, lookahead: int = 4) -> None:
        self._tokens = tokens
        self._lookahead = lookahead
        self.position = 0

    def take_matching(self, predicate: Callable[[str], bool]) -> str | None:
        limit = min(len(self._tokens), self.position + self._lookahead)
        for index in range(self.position, limit):
            if predicate(self._tokens[index]):
                self.position = index + 1
                return self._tokens[index]
        return None

    def take_many_matching(self, predicate: Callable[[str], bool], count: int) -> list[str]:
        found: list[str] = []
        for _ in range(count):
            value = self.take_matching(predicate)
            if value is None:
                break
            found.append(value)
        return found


def parse(content: bytes) -> DocumentReading:
    """Lee el RUT y devuelve los campos que el motor tributario necesita."""
    try:
        page = PdfReader(BytesIO(content)).pages[0]
    except Exception as exc:
        raise DocumentUnreadableError(
            "El archivo no es un PDF que se pueda leer.", parser=PARSER_NAME
        ) from exc

    fragments: list[str] = []

    def visitor(text: str, _cm: object, _tm: object, _font: object, _size: float) -> None:
        if text.strip():
            fragments.append(text)

    page.extract_text(visitor_text=visitor)
    full_text = "\n".join(fragments)

    values, found_template = _value_stream(fragments)
    warnings: list[ReadingWarning] = []
    if not found_template:
        warnings.append(
            ReadingWarning(
                code="TEMPLATE_MARKER_NOT_FOUND",
                message=(
                    "El RUT no tiene la forma que esperábamos: la DIAN pudo cambiar el "
                    "formato, así que conviene revisar los datos que se leyeron."
                ),
            )
        )

    fields = _extract_fields(values, warnings)
    _check_internal_consistency(fields, warnings)
    fields.append(
        ExtractedField(name="raw_text", value=full_text, confidence=Confidence.DETERMINISTIC)
    )

    log.info("documents.rut.parsed", fields=len(fields) - 1, warnings=len(warnings))
    return DocumentReading(
        doc_type="RUT",
        parser=PARSER_NAME,
        content_sha256=hashlib.sha256(content).hexdigest(),
        fields=fields,
        warnings=warnings,
    )


def _check_internal_consistency(
    fields: list[ExtractedField], warnings: list[ReadingWarning]
) -> None:
    """Verifica que el NIT y el numero de identificacion coincidan.

    En una persona natural son el mismo numero, asi que compararlos es una prueba gratuita
    de que el cursor no se desincronizo al recorrer el PDF. Es la unica defensa contra el
    peor modo de falla de un parser posicional: devolver un valor equivocado con
    aparente normalidad.
    """
    values = {f.name: f.value for f in fields}
    nit, id_number = values.get("nit"), values.get("id_number")
    kind = values.get("taxpayer_kind") or ""
    is_natural = any(marker in str(kind).lower() for marker in _NATURAL_PERSON_MARKERS)
    if not (is_natural and nit and id_number):
        return
    if str(nit) != str(id_number):
        warnings.append(
            ReadingWarning(
                code="RUT_ID_MISMATCH",
                message=(
                    f"En el RUT, el NIT ({nit}) y el número de identificación ({id_number}) "
                    "no coinciden. En una persona natural son el mismo número, así que hay "
                    "que revisar el documento."
                ),
            )
        )


def _value_stream(fragments: list[str]) -> tuple[list[str], bool]:
    """Fragmentos posteriores al ultimo bloque de plantilla: son los valores reales."""
    template_indexes = [
        i
        for i, t in enumerate(fragments)
        if _TEMPLATE_MARKER in t and len(t) >= _MIN_TEMPLATE_FRAGMENT_LEN
    ]
    if not template_indexes:
        return fragments, False
    return fragments[template_indexes[-1] + 1 :], True


# Como se llama cada campo del RUT en un aviso que lee una persona. Los avisos no deben
# filtrar nombres internos como "collection_office".
_FIELD_LABELS = {
    "form_number": "el número del formulario",
    "nit": "el NIT",
    "verification_digit": "el dígito de verificación",
    "collection_office": "la dirección seccional",
    "taxpayer_kind": "el tipo de contribuyente",
    "id_kind": "el tipo de documento",
    "id_number": "el número de identificación",
    "business_name": "la razón social",
    "last_name_1": "el primer apellido",
    "last_name_2": "el segundo apellido",
    "first_name_1": "el primer nombre",
    "other_names": "los otros nombres",
    "email": "el correo electrónico",
    "economic_activity_code": "la actividad económica",
    "economic_activity_start_date": "la fecha de inicio de actividad",
}


def _extract_fields(values: list[str], warnings: list[ReadingWarning]) -> list[ExtractedField]:
    cursor = _Cursor(values)
    fields: list[ExtractedField] = []

    def add(name: str, value: str | None, *, unit: str | None = None) -> None:
        if value is None:
            warnings.append(
                ReadingWarning(
                    code="FIELD_NOT_FOUND",
                    message=f"No se encontró {_FIELD_LABELS.get(name, name)} en el RUT",
                    # Un campo del RUT que falta no bloquea nada por si solo: lo que hace es
                    # bajar la confianza de la lectura, y de eso queda constancia.
                    needs_action=False,
                )
            )
            return
        fields.append(
            ExtractedField(name=name, value=value.strip(), confidence=Confidence.LOW, unit=unit)
        )

    def is_pure_digits(min_len: int, max_len: int) -> Callable[[str], bool]:
        def predicate(token: str) -> bool:
            digits = _join_digits(token)
            return min_len <= len(digits) <= max_len and bool(digits)

        return predicate

    def is_text(min_len: int = 2, max_len: int = 60) -> Callable[[str], bool]:
        def predicate(token: str) -> bool:
            stripped = token.strip()
            return min_len <= len(stripped) <= max_len and any(c.isalpha() for c in stripped)

        return predicate

    add("form_number", cursor.take_matching(is_pure_digits(8, 14)))

    nit_raw = cursor.take_matching(is_pure_digits(6, 10))
    add("nit", _join_digits(nit_raw) if nit_raw else None)
    add("verification_digit", cursor.take_matching(is_pure_digits(1, 1)))

    add("collection_office", cursor.take_matching(is_text(4, 60)))
    cursor.take_matching(is_pure_digits(1, 3))  # codigo de la seccional: no aporta al motor

    taxpayer_kind = cursor.take_matching(is_text(4, 60))
    add("taxpayer_kind", taxpayer_kind)
    cursor.take_matching(is_pure_digits(1, 2))  # codigo interno del tipo de contribuyente

    id_kind = cursor.take_matching(lambda t: any(label in t for label in _ID_KIND_LABELS))
    add("id_kind", id_kind)
    cursor.take_matching(is_pure_digits(1, 2))  # codigo interno del tipo de documento

    id_number_raw = cursor.take_matching(is_pure_digits(6, 10))
    add("id_number", _join_digits(id_number_raw) if id_number_raw else None)

    # Entre el numero de identificacion y el nombre va el bloque "lugar de expedicion":
    # pais, codigo, departamento, codigo, ciudad, codigo (6 fragmentos). Si no se salta
    # explicitamente, sus textos se confunden con los del nombre.
    _skip_place_block(cursor, is_text, is_pure_digits)

    is_natural_person = taxpayer_kind is not None and any(
        marker in taxpayer_kind.lower() for marker in _NATURAL_PERSON_MARKERS
    )
    if is_natural_person:
        _extract_person_name(cursor, add, is_text)
    else:
        add("business_name", cursor.take_matching(is_text(2, 120)))

    # Bloque "ubicacion" (pais+codigo+depto+codigo+ciudad+codigo) y direccion principal:
    # se saltan por la misma razon que el bloque de lugar de expedicion.
    _skip_place_block(cursor, is_text, is_pure_digits)
    cursor.take_matching(is_text(4, 80))  # direccion principal

    add("email", cursor.take_matching(lambda t: bool(_EMAIL_RE.search(t))))
    cursor.take_matching(is_pure_digits(7, 12))  # codigo postal / telefono: no se usa

    economic_activity_code = cursor.take_matching(is_pure_digits(2, 6))
    add(
        "economic_activity_code",
        _join_digits(economic_activity_code) if economic_activity_code else None,
    )
    activity_start = cursor.take_matching(is_pure_digits(8, 8))
    add("economic_activity_start_date", _as_iso_date(activity_start) if activity_start else None)

    return fields


def _extract_person_name(
    cursor: _Cursor,
    add: Callable[[str, str | None], None],
    is_text: Callable[..., Callable[[str], bool]],
) -> None:
    """Los cuatro campos del nombre vienen en cuatro fragmentos de texto consecutivos:
    primer apellido, segundo apellido, primer nombre y otros nombres. El ultimo es
    opcional, asi que si solo aparecen tres, no se fuerza el cuarto."""
    names = cursor.take_many_matching(is_text(1, 40), count=4)
    labels = ["last_name_1", "last_name_2", "first_name_1", "other_names"]
    for label, value in zip(labels, names, strict=False):
        add(label, value.strip())
    if len(names) < 3:
        add("last_name_1", None)  # fuerza el warning: con menos de 3 tokens, algo fallo


def _join_digits(token: str) -> str:
    return "".join(_DIGITS_RE.findall(token))


def _as_iso_date(digits: str) -> str | None:
    clean = _join_digits(digits)
    if len(clean) != 8:
        return None
    return f"{clean[0:4]}-{clean[4:6]}-{clean[6:8]}"


def _skip_place_block(
    cursor: _Cursor,
    is_text: Callable[..., Callable[[str], bool]],
    is_pure_digits: Callable[..., Callable[[str], bool]],
) -> None:
    """Consume pais + codigo + departamento + codigo + ciudad + codigo, sin guardarlos.

    El motor tributario no necesita el lugar de expedicion del RUT ni la ubicacion del
    contribuyente; lo relevante es no dejar que sus textos contaminen el campo siguiente.
    """
    for _ in range(3):
        cursor.take_matching(is_text(2, 40))
        cursor.take_matching(is_pure_digits(1, 3))
