"""Resumen del expediente: lo que el sistema ya sabe, derivado de lo que leyo.

Es lo que la consola del contador muestra como "lo que generamos", y es deliberadamente
menos que una declaracion: aqui no se calcula el impuesto, solo se organiza y se compara
con la ley lo que los documentos ya dicen.

Cuatro cosas:

  Obligacion       los cinco topes con lo que la DIAN reporta contra el limite legal, para
                   poder explicar por que alguien esta obligado en vez de solo afirmarlo.
  Procedencia      de donde sale cada tope: quien reporto cada valor y por que concepto, para
                   que la cifra se pueda auditar en vez de tener que creerla.
  Renglones        el agregado por casilla del formulario 210, sumando los valores que la
                   propia DIAN ya asigno a cada renglon en el reporte de exogena.
  Facturas         el total elegible para la deduccion del 1%, ya filtrado por medio de
                   pago por la DIAN.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field, computed_field

from declaras.documents.models import DocumentReading
from declaras.domain.case import CaseDetail
from declaras.parametros.casillas import casilla_en_palabras, nombre_de_casilla
from declaras.tax.obligation import ObligationAssessment, ThresholdCode, assess

_EXOGENA = "EXOGENA"
_EINVOICE_SUMMARY = "EINVOICE_SUMMARY"
_THRESHOLD_FIELD_PREFIX = "tope_"


class FormLineTotal(BaseModel):
    """Lo que la exogena aporta a un renglon del formulario 210.

    LLEVA DOS NOMBRES DEL MISMO RENGLON. `label` es el oficial del formulario ("Ingresos no
    constitutivos de renta (rentas de trabajo)"), que es el correcto y el que un contador reconoce;
    `en_palabras` es el mismo renglon dicho para quien declara una vez al ano ("Salud y pension que
    te descontaron del sueldo").

    Antes la pantalla mostraba "R100" y "R131" a secas. Poner el nombre oficial arreglo la mitad
    del problema: el numero desaparecio, pero "Ingresos no constitutivos de renta" sigue siendo
    una frase que solo entiende quien ya sabe lo que significa.
    """

    line: int
    label: str
    en_palabras: str
    amount: int
    concept_count: int


class ThresholdSource(BaseModel):
    """Un valor reportado que alimenta un tope, con quien lo reporto y por que concepto."""

    reporter_name: str | None = None
    concept: str
    amount: int
    # La DIAN compara esta fuente contra otra y toma la mayor, en vez de sumarla.
    compared_not_added: bool = False
    # El tercero reporto este valor al titular, o a otra persona.
    reported_to_titular: bool = True
    reported_name: str | None = None


class ThresholdProvenance(BaseModel):
    """De donde sale el valor de un tope.

    El valor que gobierna es siempre el que declara la DIAN (`official_amount`). Esto lo
    acompania con las fuentes que ella misma etiqueto, y con la diferencia cuando las fuentes
    no lo explican por completo. La diferencia se muestra en vez de esconderse: es informacion
    que quien firma la declaracion necesita, y taparla con una formula inventada seria
    exactamente lo que este producto no hace.
    """

    code: ThresholdCode
    official_amount: int
    sources: list[ThresholdSource] = Field(default_factory=list)

    # Las cuatro derivadas viajan en la respuesta de la API. La regla de que suma y que no es
    # de dominio (la DIAN compara algunas fuentes en vez de sumarlas), asi que recalcularla en
    # la interfaz la dejaria desincronizada el dia que la regla cambie. El `type: ignore` es
    # el patron que documenta pydantic: mypy no admite decoradores encima de `@property`.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def added_total(self) -> int:
        """Suma de las fuentes que la DIAN suma (las comparadas no son sumandos)."""
        return sum(s.amount for s in self.sources if not s.compared_not_added)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unexplained_difference(self) -> int:
        return self.official_amount - self.added_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reconciles(self) -> bool:
        return self.unexplained_difference == 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount_not_from_titular(self) -> int:
        """Cuanto de lo que suma el tope lo reporto un tercero a nombre de otra persona."""
        return sum(
            s.amount for s in self.sources if not s.compared_not_added and not s.reported_to_titular
        )


class EInvoiceTotals(BaseModel):
    invoice_count: int
    total_amount: int
    benefit_eligible_amount: int


class CaseSummary(BaseModel):
    """Resumen derivado del expediente. No es un calculo de impuesto."""

    case_id: str
    tax_year: int
    obligation: ObligationAssessment | None = None
    threshold_provenance: list[ThresholdProvenance] = Field(default_factory=list)
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
        summary.threshold_provenance = _threshold_provenance(exogena, summary.obligation)
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


def _threshold_provenance(
    exogena: DocumentReading, obligation: ObligationAssessment
) -> list[ThresholdProvenance]:
    """Agrupa por tope los valores reportados que la propia DIAN etiqueto con ese tope.

    No se deduce nada: la exogena marca en cada fila a que tope cuenta y si es una fuente que
    se compara en vez de sumarse. Aqui solo se agrupa y se ordena de mayor a menor, que es el
    orden en que alguien quiere revisar de donde sale una cifra.
    """
    official = {t.code: t.reported_amount for t in obligation.thresholds}
    grouped: dict[ThresholdCode, list[ThresholdSource]] = defaultdict(list)

    for row in exogena.rows:
        for raw_code in row.values.get("thresholds") or []:
            try:
                code = ThresholdCode(raw_code)
            except ValueError:
                continue  # un tope que el portal agregue y el sistema aun no conozca
            grouped[code].append(
                ThresholdSource(
                    reporter_name=row.values.get("reporter_name"),
                    concept=str(row.values.get("concept") or ""),
                    amount=int(row.values.get("amount") or 0),
                    compared_not_added=bool(row.values.get("compared_not_added")),
                    reported_to_titular=bool(row.values.get("reported_to_titular", True)),
                    reported_name=row.values.get("reported_name"),
                )
            )

    return [
        ThresholdProvenance(
            code=code,
            official_amount=official.get(code, 0),
            sources=sorted(grouped[code], key=lambda s: s.amount, reverse=True),
        )
        for code in ThresholdCode
        if grouped[code]
    ]


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
        FormLineTotal(
            line=line,
            label=nombre_de_casilla(line),
            en_palabras=casilla_en_palabras(line),
            amount=totals[line],
            concept_count=counts[line],
        )
        for line in sorted(totals)
    ]


def _einvoice_totals(reading: DocumentReading) -> EInvoiceTotals:
    return EInvoiceTotals(
        invoice_count=int(reading.field("invoice_count") or 0),
        total_amount=int(reading.field("total_net_amount") or 0),
        benefit_eligible_amount=int(reading.field("total_benefit_eligible_amount") or 0),
    )
