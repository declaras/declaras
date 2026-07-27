"""El 220 leído como cualquier otro documento.

Dos cosas se prueban acá y no en `test_extractor_220.py`: que la lectura conserva la
confianza que declaró el modelo (un valor estimado no puede entrar al expediente
disfrazado de certeza) y que una falla del extractor cruza la frontera convertida en
falla del dominio, sin arrastrar el texto técnico que el modelo o el SDK produjeron.
"""

from __future__ import annotations

import pytest

from declaras.documents import registry
from declaras.documents.parsers import certificados
from declaras.domain.errors import DocumentUnreadableError
from declaras.domain.models import document_label
from declaras.extraccion.f220 import Extraccion220
from tests.unit.documents.dobles import ClienteFalso

EXTRACCION = Extraccion220(
    empleador_nit="900123456", empleador_nombre="ACME SAS",
    salarios=85_000_000, cesantias_e_intereses=0, prima=0, bonificaciones=0,
    total_ingresos_brutos=85_000_000, pensiones_de_jubilacion=0,
    aportes_salud=3_400_000, aportes_pension=3_400_000, retencion=8_000_000,
    anio_gravable=2025, numero_de_certificados=1, confianza=0.97,
)


def test_lectura_220_produce_document_reading():
    lectura = certificados.leer_220(b"%PDF-x", client=ClienteFalso(EXTRACCION))
    assert lectura.doc_type == "CERT_INGRESOS_220"
    assert lectura.field("salarios") == 85_000_000
    assert lectura.field("empleador_nit") == "900123456"
    # La confianza del modelo viaja en cada campo, no se pierde.
    campo = next(f for f in lectura.fields if f.name == "salarios")
    assert campo.confidence == 0.97


def test_registry_conoce_el_220_y_no_lo_llama_deterministico():
    assert registry.reader_for("CERT_INGRESOS_220") is not None
    assert not registry.is_deterministic("CERT_INGRESOS_220")
    assert "EXOGENA" in registry.supported_types()
    assert "CERT_INGRESOS_220" in registry.supported_types()


def test_el_registry_despacha_al_lector_del_220():
    """Que la familia LLM no quede registrada apuntando a otro lector."""
    assert registry.reader_for("CERT_INGRESOS_220") is certificados.leer_220


# ─────── la frontera: lo que sale cuando el extractor falla ───────
#
# `extraer_220` levanta `ValueError` con el detalle tecnico, que es su contrato con quien
# programa. Ese texto no puede salir tal cual: la lectura la consume el expediente (que
# convierte una falla de lectura en una alerta que lee el contador) y la API. Un
# `stop_reason=refusal` ahi no dice nada y expone como esta hecho el sistema.


def test_una_falla_del_extractor_cruza_como_falla_de_dominio_sin_texto_tecnico():
    cliente = ClienteFalso(None, stop_reason="refusal")
    with pytest.raises(DocumentUnreadableError) as exc:
        certificados.leer_220(b"%PDF-x", client=cliente)
    mensaje = exc.value.message
    assert "refusal" not in mensaje
    assert "stop_reason" not in mensaje
    assert exc.value.details["parser"] == certificados.PARSER_220


def test_un_archivo_que_no_es_un_pdf_se_reporta_ilegible_sin_llamar_al_modelo():
    cliente = ClienteFalso(EXTRACCION)
    with pytest.raises(DocumentUnreadableError):
        certificados.leer_220(b"soy un JPG cualquiera", client=cliente)
    assert cliente.messages.llamadas == []  # el pre-flight del extractor sigue vigente


def test_el_certificado_tiene_nombre_legible_para_las_alertas():
    """La alerta del expediente dice "No se pudo leer {nombre}": ahi no cabe un código."""
    assert document_label("CERT_INGRESOS_220").startswith("el certificado")
