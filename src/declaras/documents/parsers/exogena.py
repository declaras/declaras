"""Lector del reporte de informacion exogena que entrega el portal en XLSX.

El archivo tiene tres bloques: un encabezado con el contribuyente y el periodo, los cinco
topes con que la DIAN determina la obligacion de declarar, y el detalle de lo que cada
tercero reporto.

El detalle trae dos columnas que valen oro y ahorran el trabajo mas duro del motor:

  Detalle                      incluye el codigo oficial del concepto, por ejemplo
                               "Otros ingresos (Concepto: 5016)"
  Uso declaracion Sugerida     dice a que renglon del formulario 210 va el valor y a que
                               tope cuenta, por ejemplo "Tope 1: Ingresos brutos | R32
                               Ingresos brutos"

Es decir: no hay que adivinar en que casilla va cada concepto reportado, porque la propia
DIAN lo indica.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, time
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from declaras.documents.models import (
    DocumentReading,
    ExtractedField,
    ExtractedRow,
    ReadingWarning,
    ThresholdCode,
)
from declaras.domain.errors import ValidationError
from declaras.observability import get_logger

log = get_logger(__name__)

PARSER_NAME = "exogena.xlsx.v1"

# Posiciones del encabezado, verificadas contra el reporte real.
_HEADER_CELLS = {
    "cutoff_date": "C3",
    "tax_year": "C4",
    "id_kind_label": "C6",
    "id_number": "C7",
    "taxpayer_name": "C8",
    "report_date": "H2",
}

# Filas de los topes y su codigo, en el orden en que el portal los imprime.
_THRESHOLD_ROWS: dict[int, ThresholdCode] = {
    15: ThresholdCode.INGRESOS,
    16: ThresholdCode.PATRIMONIO,
    17: ThresholdCode.CONSUMO_TARJETA,
    18: ThresholdCode.MOVIMIENTOS,
    19: ThresholdCode.COMPRAS,
}

_FIRST_DETAIL_ROW = 20

_COL_REPORTER_NIT = 1
_COL_REPORTER_NAME = 2
_COL_CONCEPT = 5
_COL_AMOUNT = 6
_COL_SUGGESTED_USE = 7
_COL_EXTRA = 8

_CONCEPT_CODE_RE = re.compile(r"\(Concepto:\s*(\d+)\)")
_FORM_LINE_RE = re.compile(r"\bR(\d{1,3})\b")
_THRESHOLD_LABEL_RE = re.compile(r"Tope\s*(\d)")
_REPLACEMENT_CHAR = "�"


def parse(content: bytes) -> DocumentReading:
    """Lee el reporte de exogena y devuelve sus valores con procedencia."""
    try:
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=False)
    except Exception as exc:
        raise ValidationError("el archivo no es un XLSX legible", parser=PARSER_NAME) from exc

    sheet = workbook.active
    if sheet is None:
        raise ValidationError("el XLSX no tiene hojas", parser=PARSER_NAME)

    warnings: list[ReadingWarning] = []
    fields = _read_header(sheet, warnings)
    fields += _read_thresholds(sheet)
    rows = _read_details(sheet, warnings)

    log.info(
        "documents.exogena.parsed",
        fields=len(fields),
        rows=len(rows),
        warnings=len(warnings),
    )
    return DocumentReading(
        doc_type="EXOGENA",
        parser=PARSER_NAME,
        content_sha256=hashlib.sha256(content).hexdigest(),
        fields=fields,
        rows=rows,
        warnings=warnings,
    )


# ─────────────────────────── bloques del reporte ───────────────────────────


def _read_header(sheet: Worksheet, warnings: list[ReadingWarning]) -> list[ExtractedField]:
    fields: list[ExtractedField] = []
    for name, cell in _HEADER_CELLS.items():
        raw = sheet[cell].value
        if raw is None:
            warnings.append(
                ReadingWarning(
                    code="HEADER_FIELD_MISSING",
                    message=f"el reporte no trae {name}",
                    source=cell,
                )
            )
            continue
        value: Any = _as_int(raw) if name == "tax_year" else _clean_text(raw)
        if isinstance(value, str) and _REPLACEMENT_CHAR in value:
            # El portal sirve el archivo declarando UTF-8 con bytes en ISO-8859-1, asi
            # que algunos nombres llegan con caracteres irrecuperables.
            warnings.append(
                ReadingWarning(
                    code="TEXT_ENCODING_DAMAGED",
                    message=f"{name} llega con caracteres ilegibles por el portal",
                    source=cell,
                )
            )
        fields.append(ExtractedField(name=name, value=value, source=cell))
    return fields


def _read_thresholds(sheet: Worksheet) -> list[ExtractedField]:
    """Los cinco topes de obligacion, tal como los calcula la DIAN."""
    fields: list[ExtractedField] = []
    for row, code in _THRESHOLD_ROWS.items():
        amount = _as_int(sheet.cell(row=row, column=_COL_AMOUNT).value)
        if amount is None:
            continue
        fields.append(
            ExtractedField(
                name=f"tope_{code.value}",
                value=amount,
                source=f"F{row}",
                unit="COP",
            )
        )
    return fields


def _read_details(sheet: Worksheet, warnings: list[ReadingWarning]) -> list[ExtractedRow]:
    """El detalle de lo que cada tercero reporto."""
    rows: list[ExtractedRow] = []
    for row in range(_FIRST_DETAIL_ROW, sheet.max_row + 1):
        amount = _as_int(sheet.cell(row=row, column=_COL_AMOUNT).value)
        concept = _clean_text(sheet.cell(row=row, column=_COL_CONCEPT).value)
        if amount is None or not concept:
            continue

        suggested = _clean_text(sheet.cell(row=row, column=_COL_SUGGESTED_USE).value) or ""
        rows.append(
            ExtractedRow(
                source=f"fila {row}",
                values={
                    "reporter_nit": _clean_text(
                        sheet.cell(row=row, column=_COL_REPORTER_NIT).value
                    ),
                    "reporter_name": _clean_text(
                        sheet.cell(row=row, column=_COL_REPORTER_NAME).value
                    ),
                    "concept": concept,
                    "concept_code": _concept_code(concept),
                    "amount": amount,
                    # La DIAN indica el renglon del 210 y el tope al que cuenta.
                    "form_lines": _form_lines(suggested),
                    "thresholds": _thresholds_of(suggested),
                    "suggested_use": suggested or None,
                    "extra": _clean_text(sheet.cell(row=row, column=_COL_EXTRA).value),
                },
            )
        )

    if not rows:
        warnings.append(
            ReadingWarning(
                code="NO_REPORTED_ITEMS",
                message="el reporte no trae conceptos reportados por terceros",
            )
        )
    return rows


# ─────────────────────────── utilidades ───────────────────────────


def _concept_code(concept: str) -> str | None:
    """Extrae el codigo oficial del concepto del texto del detalle."""
    match = _CONCEPT_CODE_RE.search(concept)
    return match.group(1) if match else None


def _form_lines(suggested_use: str) -> list[int]:
    """Renglones del formulario 210 a los que la DIAN asigna el valor."""
    return sorted({int(n) for n in _FORM_LINE_RE.findall(suggested_use)})


def _thresholds_of(suggested_use: str) -> list[str]:
    """Topes de obligacion a los que el valor cuenta, por su codigo interno."""
    order = list(_THRESHOLD_ROWS.values())
    found: list[str] = []
    for number in _THRESHOLD_LABEL_RE.findall(suggested_use):
        index = int(number) - 1
        if 0 <= index < len(order):
            found.append(order[index].value)
    return sorted(set(found))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        # El portal escribe fechas como datetime; se normalizan a ISO para que el valor
        # sea serializable y comparable.
        return value.date().isoformat() if value.time() == time.min else value.isoformat()
    text = str(value).strip()
    return re.sub(r"\s+", " ", text) or None


def _as_int(value: Any) -> int | None:
    """Convierte a entero los montos, que el portal escribe como texto."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    digits = re.sub(r"[^\d-]", "", str(value))
    return int(digits) if digits and digits != "-" else None
