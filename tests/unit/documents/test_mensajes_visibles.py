"""Los avisos de los lectores los lee una persona, no un desarrollador.

Cada aviso de lectura termina como un pendiente del expediente, y ahi lo puede ver quien va a
firmar la declaracion. Sin una regla que lo impida, la mitad de los avisos quedan escritos como
notas internas ("no se encontro el encabezado de la tabla"): en minuscula, sin tildes y con
nombres de campo del codigo. Paso, y estos casos son lo que evita que vuelva a pasar.
"""

from __future__ import annotations

import re

import pytest

from declaras.documents.parsers import einvoice_summary, exogena, renta_210, rut
from tests.documents_fixtures import (
    build_einvoice_summary_xlsx,
    build_exogena_xlsx,
    build_renta_210_pdf,
    build_rut_pdf,
)

# Un identificador del codigo que se escapo al texto: dos palabras unidas por guion bajo.
_IDENTIFICADOR = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _todos_los_avisos():
    """Avisos de los cuatro lectores, forzando los casos que los producen."""
    lecturas = [
        exogena.parse(build_exogena_xlsx(taxpayer_name="PEREZ JOS�")),
        exogena.parse(
            build_exogena_xlsx(
                taxpayer_name="VALENCIA MORENO JUAN JOSE",
                detail_rows=[
                    {
                        "reporter_nit": "900111222",
                        "reporter_name": "ZPN ARQUIREDES SAS",
                        "concept": "Servicios (Concepto: 5004)",
                        "amount": 7_330_000,
                        "suggested_use": "Tope 1: Ingresos brutos",
                        "reported_name": "Alejandra Delgado Bautista",
                    }
                ],
            )
        ),
        rut.parse(build_rut_pdf()),
        einvoice_summary.parse(build_einvoice_summary_xlsx()),
        renta_210.parse(build_renta_210_pdf(patrimonio_liquido=99_000_000)),
        renta_210.parse(build_renta_210_pdf()),
    ]
    return [w for lectura in lecturas for w in lectura.warnings]


@pytest.mark.parametrize("aviso", _todos_los_avisos(), ids=lambda w: w.code)
def test_el_aviso_esta_escrito_para_una_persona(aviso):
    assert aviso.message[0].isupper(), f"empieza en minúscula: {aviso.message!r}"
    assert aviso.message.rstrip().endswith(
        ("."),
    ), f"no termina en punto: {aviso.message!r}"
    filtrado = _IDENTIFICADOR.search(aviso.message)
    assert filtrado is None, f"filtra un nombre del código ({filtrado.group()}): {aviso.message!r}"


def test_hay_avisos_que_probar():
    """Si los fixtures dejaran de producir avisos, los casos de arriba pasarian sin probar nada."""
    codigos = {w.code for w in _todos_los_avisos()}
    assert codigos >= {
        "TEXT_ENCODING_DAMAGED",
        "REPORTED_TO_ANOTHER_PERSON",
        "FORM_ARITHMETIC_MISMATCH",
    }
