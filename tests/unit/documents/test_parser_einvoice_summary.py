"""El resumen de facturas alimenta la deduccion del 1%, ya filtrada por medio de pago."""

from __future__ import annotations

from declaras.documents.parsers.einvoice_summary import parse
from tests.documents_fixtures import build_einvoice_summary_xlsx


def test_lee_el_encabezado():
    reading = parse(build_einvoice_summary_xlsx(tax_year=2024, id_number="99887766"))
    assert reading.field("tax_year") == 2024
    assert reading.field("id_number") == "99887766"


def test_agrega_el_total_neto_y_el_elegible_para_el_beneficio():
    reading = parse(
        build_einvoice_summary_xlsx(
            invoices=[
                {
                    "issuer_nit": "1",
                    "issuer_name": "A",
                    "issue_date": "2025-01-01",
                    "invoiced_amount": 100,
                    "net_amount": 100,
                    "benefit_eligible_amount": 100,
                    "payment_method": "Electrónicos",
                    "invoice_number": "F1",
                    "cufe": "x",
                },
                {
                    "issuer_nit": "2",
                    "issuer_name": "B",
                    "issue_date": "2025-02-01",
                    "invoiced_amount": 50,
                    "net_amount": 50,
                    "benefit_eligible_amount": 0,
                    "payment_method": "Efectivo",
                    "invoice_number": "F2",
                    "cufe": "y",
                },
            ]
        )
    )
    assert reading.field("invoice_count") == 2
    assert reading.field("total_net_amount") == 150
    assert reading.field("total_benefit_eligible_amount") == 100, (
        "el pago en efectivo no debe contar para la deduccion del 1%"
    )


def test_una_factura_en_efectivo_con_beneficio_genera_aviso():
    """Serial de inconsistencia: si vino en efectivo, el beneficio deberia ser cero."""
    reading = parse(
        build_einvoice_summary_xlsx(
            invoices=[
                {
                    "issuer_nit": "1",
                    "issuer_name": "A",
                    "issue_date": "2025-01-01",
                    "invoiced_amount": 100,
                    "net_amount": 100,
                    "benefit_eligible_amount": 100,
                    "payment_method": "Efectivo",
                    "invoice_number": "F1",
                    "cufe": "x",
                },
            ]
        )
    )
    assert any(w.code == "CASH_PAYMENT_WITH_BENEFIT" for w in reading.warnings)


def test_sin_facturas_el_total_es_cero():
    reading = parse(build_einvoice_summary_xlsx(invoices=[]))
    assert reading.field("invoice_count") == 0
    assert reading.field("total_net_amount") == 0
