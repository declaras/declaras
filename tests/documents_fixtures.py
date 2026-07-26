"""Constructores de documentos sinteticos para pruebas.

Los archivos reales de un contribuyente no se versionan (traen datos personales y
financieros reales). Estos constructores reproducen la MISMA estructura que valida cada
parser, calibrada contra un documento real, pero con datos inventados.
"""

from __future__ import annotations

from io import BytesIO

from fpdf import FPDF
from openpyxl import Workbook


def build_exogena_xlsx(
    *,
    tax_year: int = 2025,
    id_number: str = "1000000001",
    taxpayer_name: str = "PEREZ GOMEZ ANA MARIA",
    thresholds: dict[str, int] | None = None,
    detail_rows: list[dict] | None = None,
) -> bytes:
    """Reproduce el reporte de informacion exogena, calibrado contra el XLSX real."""
    thresholds = thresholds or {
        "ingresos": 10_000_000,
        "patrimonio": 5_000_000,
        "consumo_tarjeta": 1_000_000,
        "movimientos": 20_000_000,
        "compras": 500_000,
    }
    detail_rows = (
        detail_rows
        if detail_rows is not None
        else [
            {
                "reporter_nit": "900111222",
                "reporter_name": "EMPRESA DEMO SAS",
                "concept": "Valor ingreso laboral promedio (Concepto: 2276)",
                "amount": 3_000_000,
                "suggested_use": "R36 Otras rentas exentas",
            },
            {
                "reporter_nit": "900333444",
                "reporter_name": "BANCO DEMO",
                "concept": "Retencion en la fuente (Concepto: 5004)",
                "amount": 150_000,
                "suggested_use": "R132 Retenciones año gravable a declarar",
            },
        ]
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"
    ws["C1"] = "Consulta de Información reportada por terceros"
    ws["G2"] = "Fecha   Reporte:"
    ws["H2"] = "2026-01-01 00:00:00"
    ws["A3"] = "Fecha corte del proceso: "
    ws["C3"] = "2025-12-31 00:00:00"
    ws["A4"] = "Año al que se refiere la consulta:"
    ws["C4"] = str(tax_year)
    ws["A6"] = "Tipo de documento:"
    ws["C6"] = "C. C."
    ws["A7"] = "Identificación:"
    ws["C7"] = id_number
    ws["A8"] = "Nombres / Razón social:"
    ws["C8"] = taxpayer_name

    ws["A14"] = "NIT"
    ws["B14"] = "Nombre / Razón Social"
    ws["C14"] = "NIT"
    ws["D14"] = "Nombre/Razón Social reportada por el tercero"
    ws["E14"] = "Detalle"
    ws["F14"] = "Valor"
    ws["G14"] = "Uso declaración Sugerida"

    threshold_order = ["ingresos", "patrimonio", "consumo_tarjeta", "movimientos", "compras"]
    labels = [
        "Tope 1 - Ingresos",
        "Tope 2 - Patrimonio",
        "Tope 3 - Consumo TC",
        "Tope 4 - Movimiento",
        "Tope 5 - Compras",
    ]
    for offset, (code, label) in enumerate(zip(threshold_order, labels, strict=True)):
        row = 15 + offset
        ws.cell(row=row, column=5, value=label)
        ws.cell(row=row, column=6, value=thresholds[code])

    for offset, item in enumerate(detail_rows):
        row = 20 + offset
        ws.cell(row=row, column=1, value=item["reporter_nit"])
        ws.cell(row=row, column=2, value=item["reporter_name"])
        ws.cell(row=row, column=3, value=id_number)
        ws.cell(row=row, column=4, value=taxpayer_name)
        ws.cell(row=row, column=5, value=item["concept"])
        ws.cell(row=row, column=6, value=item["amount"])
        ws.cell(row=row, column=7, value=item["suggested_use"])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_einvoice_summary_xlsx(
    *,
    tax_year: int = 2025,
    id_number: str = "1000000001",
    taxpayer_name: str = "PEREZ GOMEZ ANA MARIA",
    invoices: list[dict] | None = None,
) -> bytes:
    """Reproduce el resumen de facturas electronicas, calibrado contra el XLSX real."""
    invoices = (
        invoices
        if invoices is not None
        else [
            {
                "issuer_nit": "900555666",
                "issuer_name": "SUPERMERCADO DEMO SAS",
                "issue_date": "2025-03-15",
                "invoiced_amount": 120_000,
                "net_amount": 120_000,
                "benefit_eligible_amount": 120_000,
                "payment_method": "Electrónicos",
                "invoice_number": "F001",
                "cufe": "abc123",
            },
            {
                "issuer_nit": "900777888",
                "issuer_name": "TIENDA DEMO",
                "issue_date": "2025-05-20",
                "invoiced_amount": 50_000,
                "net_amount": 50_000,
                "benefit_eligible_amount": 0,
                "payment_method": "Efectivo",
                "invoice_number": "F002",
                "cufe": "def456",
            },
        ]
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"], ws["B1"] = "Nombre", "Informe de Facturas electrónicas adquiridas"
    ws["A3"], ws["B3"] = "Año Gravable", str(tax_year)
    ws["A4"], ws["B4"] = "Tipo Documento Adquiriente", "13 Cédula de Ciudadanía"
    ws["A5"], ws["B5"] = "Doc Identificacion Adq.", id_number
    ws["A6"], ws["B6"] = "NIT", id_number
    ws["A7"], ws["B7"] = "Nombre o razón social", taxpayer_name

    headers = [
        "Identificación Emisor Factura",
        "Nombre Emisor Factura",
        "Fecha Emisión",
        "Valor Facturado",
        "Valor Notas Crédito",
        "Valor Notas Débito",
        "Valor Factura / Afectada con Notas",
        "Valor Susceptible Beneficio",
        "Medios De Pago",
        "Num_factura_venta",
        "CUFE",
    ]
    for col, label in enumerate(headers, start=1):
        ws.cell(row=25, column=col, value=label)

    for offset, inv in enumerate(invoices):
        row = 26 + offset
        ws.cell(row=row, column=1, value=inv["issuer_nit"])
        ws.cell(row=row, column=2, value=inv["issuer_name"])
        ws.cell(row=row, column=3, value=inv["issue_date"])
        ws.cell(row=row, column=4, value=inv["invoiced_amount"])
        ws.cell(row=row, column=5, value=0)
        ws.cell(row=row, column=6, value=0)
        ws.cell(row=row, column=7, value=inv["net_amount"])
        ws.cell(row=row, column=8, value=inv["benefit_eligible_amount"])
        ws.cell(row=row, column=9, value=inv["payment_method"])
        ws.cell(row=row, column=10, value=inv["invoice_number"])
        ws.cell(row=row, column=11, value=inv["cufe"])

    footer_row = 26 + len(invoices) + 1
    ws.cell(row=footer_row, column=1, value=f"{len(invoices)} Facturas procesadas disponibles")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_rut_pdf(
    *,
    form_number: str = "999888777666",
    nit: str = "1000000001",
    verification_digit: str = "5",
    collection_office: str = "Impuestos de Medellin",
    taxpayer_kind: str = "Persona natural o sucesión ilíquida",
    id_kind: str = "Cédula de Ciudadanía",
    id_number: str = "1000000001",
    last_name_1: str = "PEREZ",
    last_name_2: str = "GOMEZ",
    first_name_1: str = "ANA",
    other_names: str = "MARIA",
    email: str = "ana.perez@example.com",
    economic_activity_code: str = "6201",
    activity_start_date: str = "20200115",
) -> bytes:
    """Reproduce el RUT en PDF: un bloque de plantilla y luego los valores en orden
    secuencial, tal como lo dibuja el generador real del portal (calibrado el
    2026-07-25 contra un RUT real)."""

    def spaced(digits: str) -> str:
        return " ".join(digits)

    template_blob = "IDENTIFICACIÓN " + ("Formulario del Registro Único Tributario " * 15)
    value_tokens = [
        "Actualización de oficio",
        "1 3",
        form_number,
        spaced(nit),
        f" {verification_digit}",
        f" {collection_office}",
        " 3 2",
        taxpayer_kind,
        " 2",
        f" {id_kind}",
        " 1 3",
        f" {spaced(id_number)}",
        "COLOMBIA",
        " 1 6 9",
        " Antioquia",
        " 0 5",
        "Medellín",
        " 0 0 1",
        last_name_1,
        f" {last_name_2}",
        f" {first_name_1}",
        f" {other_names}",
        "COLOMBIA",
        " 1 6 9",
        " Antioquia",
        " 0 5",
        "Medellín",
        " 0 0 1",
        "CL 10 # 20 - 30",
        email,
        "   3001234567",
        spaced(economic_activity_code),
        spaced(activity_start_date),
    ]

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", size=7)
    # Una sola llamada de texto: en el RUT real la plantilla se dibuja como UN fragmento
    # contiguo, no envuelto en varias lineas. Reproducirlo asi es lo que hace que el
    # detector de "fin de la plantilla" se comporte igual que con el documento real.
    pdf.text(10, 15, template_blob)

    y = 120
    for token in value_tokens:
        pdf.set_xy(10, y)
        pdf.cell(0, 4, token)
        y += 4.2

    return bytes(pdf.output())
