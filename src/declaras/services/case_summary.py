"""Resumen del expediente: lo que el sistema ya sabe, derivado de lo que leyo.

Es lo que la consola del contador muestra como "lo que generamos", y es deliberadamente
menos que una declaracion: aqui no se calcula el impuesto, solo se organiza y se compara
con la ley lo que los documentos ya dicen.

Tres cosas:

  Obligacion       los cinco topes con lo que la DIAN reporta contra el limite legal, para
                   poder explicar por que alguien esta obligado en vez de solo afirmarlo.
  Renglones        el agregado por casilla del formulario 210, sumando los valores que la
                   propia DIAN ya asigno a cada renglon en el reporte de exogena.
  Facturas         el total elegible para la deduccion del 1%, ya filtrado por medio de
                   pago por la DIAN.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from declaras.documents.models import DocumentReading
from declaras.domain.case import CaseDetail
from declaras.tax.obligation import ObligationAssessment, ThresholdCode, assess

_EXOGENA = "EXOGENA"
_EINVOICE_SUMMARY = "EINVOICE_SUMMARY"
_THRESHOLD_FIELD_PREFIX = "tope_"


class FormLineTotal(BaseModel):
    """Lo que la exogena aporta a un renglon del formulario 210."""

    line: int
    amount: int
    concept_count: int


class EInvoiceTotals(BaseModel):
    invoice_count: int
    total_amount: int
    benefit_eligible_amount: int


class CaseSummary(BaseModel):
    """Resumen derivado del expediente. No es un calculo de impuesto."""

    case_id: str
    tax_year: int
    obligation: ObligationAssessment | None = None
    form_lines: list[FormLineTotal] = Field(default_factory=list)
    einvoices: EInvoiceTotals | None = None
    documents_read: int = 0
    documents_pending_reading: int = 0
    open_flags: int = 0

    @property
    def has_data(self) -> bool:
        return self.obligation is not None or bool(self.form_lines)


def build_summary(detail: CaseDetail) -> CaseSummary:
    """Arma el resumen a partir de las lecturas que ya tiene el expediente."""
    readings = {d.doc_type: d.reading for d in detail.documents if d.reading is not None}

    summary = CaseSummary(
        case_id=str(detail.case.id),
        tax_year=detail.case.tax_year,
        documents_read=sum(1 for d in detail.documents if d.reading is not None),
        documents_pending_reading=sum(1 for d in detail.documents if d.reading is None),
        open_flags=len(detail.open_flags),
    )

    exogena = readings.get(_EXOGENA)
    if exogena is not None:
        summary.obligation = _assess_obligation(exogena, detail.case.tax_year)
        summary.form_lines = _aggregate_form_lines(exogena)

    einvoices = readings.get(_EINVOICE_SUMMARY)
    if einvoices is not None:
        summary.einvoices = _einvoice_totals(einvoices)

    return summary


def _assess_obligation(exogena: DocumentReading, tax_year: int) -> ObligationAssessment:
    """Los topes que la DIAN reporta, comparados con el limite legal del anio."""
    reported: dict[ThresholdCode, int] = {}
    for field in exogena.fields:
        if not field.name.startswith(_THRESHOLD_FIELD_PREFIX):
            continue
        code_value = field.name.removeprefix(_THRESHOLD_FIELD_PREFIX)
        try:
            code = ThresholdCode(code_value)
        except ValueError:
            continue  # un tope que el portal agregue y el sistema aun no conozca
        reported[code] = int(field.value or 0)
    return assess(tax_year=tax_year, reported=reported)


def _aggregate_form_lines(exogena: DocumentReading) -> list[FormLineTotal]:
    """Suma por renglon del 210, usando la asignacion que hace la propia DIAN.

    Un mismo valor reportado puede contar para varios renglones (la DIAN lo indica asi),
    en cuyo caso se suma en todos: el reporte no dice como repartirlo, y presentarlo
    completo en cada renglon es lo que le permite a un contador decidir.
    """
    totals: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    for row in exogena.rows:
        amount = int(row.values.get("amount") or 0)
        for line in row.values.get("form_lines") or []:
            totals[int(line)] += amount
            counts[int(line)] += 1
    return [
        FormLineTotal(line=line, amount=totals[line], concept_count=counts[line])
        for line in sorted(totals)
    ]


def _einvoice_totals(reading: DocumentReading) -> EInvoiceTotals:
    return EInvoiceTotals(
        invoice_count=int(reading.field("invoice_count") or 0),
        total_amount=int(reading.field("total_net_amount") or 0),
        benefit_eligible_amount=int(reading.field("total_benefit_eligible_amount") or 0),
    )
