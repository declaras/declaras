"""Lector del formulario 210 (declaracion de renta de personas naturales) en PDF.

COMO ESTA HECHO Y POR QUE ASI

El PDF que entrega el portal no tiene campos de formulario y el fondo con los numeros de
casilla es una imagen, asi que no hay forma de pedir "la casilla 29" por su nombre. Lo que
si es exacto es la posicion: cada valor se dibuja con su propio operador `Tm`, que trae las
coordenadas del texto. El parser lee esos operadores del flujo de contenido del PDF y ubica
cada valor por su posicion:

  * la coordenada vertical identifica la fila del formulario (`_ROWS`);
  * la coordenada horizontal identifica la columna dentro de esa fila.

Se leen las coordenadas del flujo en vez de usar el extractor de texto de la libreria
porque el extractor agrupa trazos y no reporta la matriz de todos, y sin coordenadas no hay
forma de saber a que casilla pertenece un numero.

Las filas no comparten un solo juego de columnas: la franja del patrimonio, la de la cedula
general y la de totales tienen anchos distintos, asi que cada fila declara cual usa. Los
valores se imprimen alineados a la derecha, por lo que la coordenada de inicio depende del
ancho del numero; por eso cada columna es una banda y no una posicion.

ALCANCE DELIBERADAMENTE PARCIAL: se mapea el patrimonio y la cedula general completa, que es
lo que el motor necesita para verificar. Las cedulas de pensiones y de dividendos, y las
ganancias ocasionales, no estan mapeadas todavia. Un mapa incompleto es honesto; un mapa
adivinado seria peligroso, porque un valor puesto en la casilla equivocada corrompe la
comparacion patrimonial, que es justo la validacion que protege al declarante.

COMO SE CALIBRO: rasterizando el PDF y transcribiendo los numeros de casilla impresos, no
deduciendolos del orden de los valores.

VALIDACION: el formulario tiene identidades aritmeticas propias (el patrimonio liquido es el
bruto menos las deudas, la renta liquida es los ingresos menos lo no constitutivo). Si los
valores leidos no las cumplen, el mapa se desincronizo y se emite un aviso en vez de
entregar numeros equivocados en silencio.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from itertools import pairwise

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

PARSER_NAME = "renta210.pdf.v2"

# Un valor dibujado con su posicion: `1 0 0 1 <x> <y> Tm (<texto>)Tj`. El texto puede traer
# parentesis escapados, asi que se acepta cualquier caracter escapado dentro de la cadena.
_POSITIONED_TEXT_RE = re.compile(
    r"1 0 0 1 (-?[\d.]+) (-?[\d.]+) Tm\s*\(((?:[^()\\]|\\.)*)\)\s*Tj",
    re.DOTALL,
)
_AMOUNT_RE = re.compile(r"^[\d,]+$")

_TOLERANCE = 1_000  # el formulario redondea al millar, asi que las identidades toleran eso

# Margen vertical para reconocer una fila. La DIAN regenera el PDF en cada descarga y la
# linea base puede moverse una fraccion de punto; exigir la coordenada exacta haria que una
# diferencia invisible dejara el formulario sin leer. No se sube de 1.0 porque hay dos filas
# de la cabecera separadas por solo 2 puntos.
_ROW_TOLERANCE = 1.0

# Bandas horizontales de cada juego de columnas. Se abren hacia la izquierda mas alla de lo
# observado porque el valor crece hacia ese lado: un numero de mas digitos empieza antes.
_CEDULA_COLUMNS = ((140.0, 235.0), (255.0, 355.0), (375.0, 475.0), (495.0, 592.0))
_PATRIMONIO_COLUMNS = ((140.0, 260.0), (300.0, 420.0), (500.0, 592.0))
_TOTALS_COLUMNS = ((100.0, 175.0), (240.0, 312.0), (360.0, 450.0), (500.0, 592.0))
_RIGHTMOST_COLUMN = ((500.0, 592.0),)


@dataclass(frozen=True)
class _Positioned:
    """Un trazo de texto del PDF con la posicion en que se dibujo."""

    x: float
    y: float
    text: str


@dataclass(frozen=True)
class FormRow:
    """Una fila del formulario: su posicion vertical y las casillas que dibuja."""

    y: float
    # Una casilla por columna del juego que usa la fila. `None` significa que el formulario
    # sombrea esa celda: la columna no aplica a esta fila.
    boxes: tuple[int | None, ...]
    columns: tuple[tuple[float, float], ...]
    label: str


# Filas de la cedula general. Las cuatro columnas son, en orden:
#   0. Rentas de trabajo
#   1. Rentas de trabajo que no provengan de una relacion laboral (honorarios)
#   2. Rentas de capital
#   3. Rentas no laborales
_ROWS: tuple[FormRow, ...] = (
    FormRow(605.5, (28,), _RIGHTMOST_COLUMN, "1% de compras con factura electronica"),
    FormRow(593.5, (29, 30, 31), _PATRIMONIO_COLUMNS, "patrimonio"),
    FormRow(569.5, (32, 43, 58, 74), _CEDULA_COLUMNS, "ingresos brutos"),
    FormRow(557.5, (None, None, None, 75), _CEDULA_COLUMNS, "devoluciones y descuentos"),
    FormRow(545.5, (33, 44, 59, 76), _CEDULA_COLUMNS, "ingresos no constitutivos de renta"),
    FormRow(533.5, (None, 45, 60, 77), _CEDULA_COLUMNS, "costos y deducciones procedentes"),
    FormRow(521.5, (34, 46, 61, 78), _CEDULA_COLUMNS, "renta liquida"),
    FormRow(509.5, (None, None, 62, 79), _CEDULA_COLUMNS, "rentas liquidas pasivas ECE"),
    FormRow(497.5, (35, 47, 63, 80), _CEDULA_COLUMNS, "aportes voluntarios AFC, FVP y AVC"),
    FormRow(485.5, (36, 48, 64, 81), _CEDULA_COLUMNS, "otras rentas exentas"),
    FormRow(473.5, (37, 49, 65, 82), _CEDULA_COLUMNS, "total rentas exentas"),
    FormRow(461.5, (38, 50, 66, 83), _CEDULA_COLUMNS, "intereses de vivienda"),
    FormRow(449.5, (39, 51, 67, 84), _CEDULA_COLUMNS, "otras deducciones imputables"),
    FormRow(437.5, (40, 52, 68, 85), _CEDULA_COLUMNS, "total deducciones imputables"),
    FormRow(425.5, (41, 53, 69, 86), _CEDULA_COLUMNS, "rentas exentas y deducciones limitadas"),
    FormRow(413.5, (None, 54, 70, 87), _CEDULA_COLUMNS, "renta liquida ordinaria del ejercicio"),
    FormRow(401.5, (None, 55, 71, 88), _CEDULA_COLUMNS, "perdida liquida del ejercicio"),
    FormRow(389.5, (None, 56, 72, 89), _CEDULA_COLUMNS, "compensaciones por perdidas"),
    FormRow(377.5, (42, 57, 73, 90), _CEDULA_COLUMNS, "renta liquida ordinaria"),
    # Franja de totales: sus cuatro casillas van seguidas en una sola linea, con anchos
    # distintos a los de la cedula.
    FormRow(365.5, (91, 92, 93, 94), _TOTALS_COLUMNS, "totales de la cedula general"),
    FormRow(353.5, (95, 96, 97, 98), _TOTALS_COLUMNS, "renta gravable de la cedula general"),
)

# Posicion vertical de los datos del declarante en la cabecera.
_TAX_YEAR_Y = 722.5
_ID_NUMBER_Y = 615.5
# El digito de verificacion va despues de un espacio mayor que el que separa los digitos del
# numero (unos 11 puntos), asi que un salto mas grande marca donde termina la cedula.
_DIGIT_GAP = 13.0

# Nombre de cada casilla tal como lo imprime el formulario, para que la lectura se explique
# sola y la interfaz nunca tenga que mostrar un numero de casilla a secas.
BOX_LABELS: dict[int, str] = {
    28: "Uno por ciento (1%) de compras con factura electrónica",
    29: "Total patrimonio bruto",
    30: "Deudas",
    31: "Total patrimonio líquido",
    91: "Renta líquida cédula general",
    92: "Rentas exentas y deducciones imputables (limitadas)",
    93: "Renta líquida ordinaria cédula general",
    94: "Compensación de pérdidas año 2018 y anteriores",
    95: "Compensación por exceso de renta presuntiva",
    96: "Rentas gravables",
    97: "Renta líquida gravable cédula general",
    98: "Renta presuntiva",
}

# Las filas de la cedula general repiten el mismo concepto en las cuatro columnas, asi que
# la etiqueta se arma sola: concepto de la fila mas nombre de la columna.
_COLUMN_NAMES = ("rentas de trabajo", "honorarios", "rentas de capital", "rentas no laborales")
_CONCEPTS: dict[float, str] = {
    569.5: "Ingresos brutos",
    557.5: "Devoluciones, rebajas y descuentos",
    545.5: "Ingresos no constitutivos de renta",
    533.5: "Costos y deducciones procedentes",
    521.5: "Renta líquida",
    509.5: "Rentas líquidas pasivas ECE",
    497.5: "Aportes voluntarios AFC, FVP y AVC",
    485.5: "Otras rentas exentas",
    473.5: "Total rentas exentas",
    461.5: "Intereses de vivienda",
    449.5: "Otras deducciones imputables",
    437.5: "Total deducciones imputables",
    425.5: "Rentas exentas y deducciones imputables (limitadas)",
    413.5: "Renta líquida ordinaria del ejercicio",
    401.5: "Pérdida líquida del ejercicio",
    389.5: "Compensaciones por pérdidas",
    377.5: "Renta líquida ordinaria",
}
for _row in _ROWS:
    _concept = _CONCEPTS.get(_row.y)
    if _concept is None:
        continue
    for _position, _box in enumerate(_row.boxes):
        if _box is not None:
            BOX_LABELS.setdefault(_box, f"{_concept} ({_COLUMN_NAMES[_position]})")


def parse(content: bytes) -> DocumentReading:
    """Lee un formulario 210 y devuelve las casillas que el motor necesita."""
    marks, raw_text = _read_pdf(content)

    warnings: list[ReadingWarning] = []
    boxes = _read_boxes(marks, warnings)

    fields = [
        ExtractedField(
            name=f"casilla_{number}",
            value=amount,
            confidence=Confidence.LOW,
            source=BOX_LABELS.get(number, f"casilla {number}"),
            unit="COP",
        )
        for number, amount in sorted(boxes.items())
    ]
    fields.append(
        ExtractedField(
            name="tax_year",
            value=_read_digits_at(marks, _TAX_YEAR_Y),
            confidence=Confidence.LOW,
            source="año gravable",
        )
    )
    fields.append(
        ExtractedField(
            name="id_number",
            value=_read_digits_at(marks, _ID_NUMBER_Y, stop_at_gap=True),
            confidence=Confidence.LOW,
            source="número de identificación tributaria",
        )
    )
    fields.append(
        ExtractedField(name="raw_text", value=raw_text, confidence=Confidence.DETERMINISTIC)
    )

    _check_form_arithmetic(boxes, warnings)
    if not boxes:
        warnings.append(
            ReadingWarning(
                code="FORM_LAYOUT_NOT_RECOGNIZED",
                message=(
                    "No se reconoció la disposición del formulario, así que no se pudieron "
                    "ubicar las casillas. Puede ser otra versión del 210."
                ),
            )
        )

    log.info("documents.renta210.parsed", boxes=len(boxes), warnings=len(warnings))
    return DocumentReading(
        doc_type="RENTA_210",
        parser=PARSER_NAME,
        content_sha256=hashlib.sha256(content).hexdigest(),
        fields=fields,
        warnings=warnings,
    )


def _read_pdf(content: bytes) -> tuple[list[_Positioned], str]:
    """Extrae los trazos con posicion y el texto plano de la primera pagina."""
    try:
        page = PdfReader(BytesIO(content)).pages[0]
        contents = page.get_contents()
        stream = b"" if contents is None else contents.get_data()
        raw_text = page.extract_text()
    except Exception as exc:
        raise DocumentUnreadableError(
            "El archivo no es un PDF que se pueda leer.", parser=PARSER_NAME
        ) from exc

    marks = [
        _Positioned(x=float(x), y=float(y), text=text.strip())
        for x, y, text in _POSITIONED_TEXT_RE.findall(stream.decode("latin-1"))
        if text.strip()
    ]
    return marks, raw_text


def _read_boxes(marks: list[_Positioned], warnings: list[ReadingWarning]) -> dict[int, int]:
    """Ubica cada monto en su casilla segun la fila y la columna en que se dibujo."""
    boxes: dict[int, int] = {}
    shaded_hits = 0

    for mark in marks:
        amount = _as_amount(mark.text)
        if amount is None:
            continue
        row = _row_at(mark.y)
        if row is None:
            continue
        column = _column_of(mark.x, row)
        if column is None or column >= len(row.boxes):
            continue
        box = row.boxes[column]
        if box is None:
            # El formulario sombrea esta celda, asi que no deberia traer un valor: es senal
            # de que el mapa de filas no corresponde a esta version del formulario.
            shaded_hits += 1
            continue
        boxes[box] = amount

    if shaded_hits:
        warnings.append(
            ReadingWarning(
                code="FORM_LAYOUT_NOT_RECOGNIZED",
                message=(
                    f"Se encontraron {shaded_hits} valores en celdas que el formulario deja "
                    "en blanco, así que puede ser otra versión del 210."
                ),
            )
        )
    return boxes


def _row_at(y: float) -> FormRow | None:
    """Fila del formulario cuya linea base coincide con esta coordenada vertical."""
    best: FormRow | None = None
    distance = _ROW_TOLERANCE
    for row in _ROWS:
        candidate = abs(row.y - y)
        if candidate <= distance:
            best, distance = row, candidate
    return best


def _column_of(x: float, row: FormRow) -> int | None:
    """Indice de la columna de la fila en que cae una coordenada horizontal."""
    for index, (start, end) in enumerate(row.columns):
        if start <= x <= end:
            return index
    return None


def _read_digits_at(marks: list[_Positioned], y: float, *, stop_at_gap: bool = False) -> int | None:
    """Reconstruye un numero que el formulario imprime digito por digito.

    El ano gravable y la cedula se dibujan en casillas individuales, un digito en cada una,
    asi que hay que recomponerlos ordenando por posicion horizontal.
    """
    digits = sorted(
        (m for m in marks if abs(m.y - y) <= _ROW_TOLERANCE and m.text.isdigit()),
        key=lambda m: m.x,
    )
    if not digits:
        return None

    taken = [digits[0]]
    for previous, current in pairwise(digits):
        if stop_at_gap and current.x - previous.x > _DIGIT_GAP:
            break
        taken.append(current)
    return int("".join(m.text for m in taken))


def _check_form_arithmetic(boxes: dict[int, int], warnings: list[ReadingWarning]) -> None:
    """Comprueba las identidades del propio formulario.

    Es la defensa contra el peor modo de falla de un parser posicional: entregar el valor de
    una casilla en el lugar de otra sin que nada lo delate.
    """
    identities: list[tuple[str, int, tuple[int, ...], tuple[int, ...]]] = [
        ("patrimonio líquido", 31, (29,), (30,)),
        ("renta líquida de trabajo", 34, (32,), (33,)),
        ("renta líquida de capital", 61, (58,), (59, 60)),
        ("renta líquida no laboral", 78, (74,), (75, 76, 77)),
        ("total rentas exentas de trabajo", 37, (35, 36), ()),
        ("total deducciones imputables de trabajo", 40, (38, 39), ()),
        ("renta líquida ordinaria de trabajo", 42, (34,), (41,)),
        ("renta líquida de la cédula general", 91, (34, 46, 61, 78), ()),
        ("renta líquida ordinaria de la cédula general", 93, (91,), (92,)),
    ]
    for name, result, added, subtracted in identities:
        if result not in boxes or any(b not in boxes for b in added + subtracted):
            continue
        expected = sum(boxes[b] for b in added) - sum(boxes[b] for b in subtracted)
        # El formulario nunca imprime valores negativos: si la resta da negativo, la casilla
        # queda en cero.
        expected = max(expected, 0)
        if abs(boxes[result] - expected) > _TOLERANCE:
            warnings.append(
                ReadingWarning(
                    code="FORM_ARITHMETIC_MISMATCH",
                    message=(
                        f"La casilla {result} ({name}) no cuadra con las casillas que la "
                        "componen, así que la lectura del PDF pudo desincronizarse."
                    ),
                    source=BOX_LABELS.get(result, f"casilla {result}"),
                )
            )


def _as_amount(text: str) -> int | None:
    clean = text.strip()
    if not _AMOUNT_RE.fullmatch(clean):
        return None
    digits = clean.replace(",", "")
    return int(digits) if digits else None
