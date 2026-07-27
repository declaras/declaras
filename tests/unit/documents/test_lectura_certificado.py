"""El 220 leído como cualquier otro documento.

Dos cosas se prueban acá y no en `test_extractor_220.py`: que la lectura conserva la
confianza que declaró el modelo (un valor estimado no puede entrar al expediente
disfrazado de certeza) y que una falla del extractor cruza la frontera convertida en
falla del dominio, sin arrastrar el texto técnico que el modelo o el SDK produjeron.
"""

from __future__ import annotations

import pytest

from declaras.documents import registry
from declaras.documents.models import DocumentReading
from declaras.documents.parsers import certificados, exogena
from declaras.documents.service import DocumentReaderService
from declaras.domain.errors import DocumentReaderUnavailableError, DocumentUnreadableError
from declaras.domain.models import document_label
from declaras.extraccion.f220 import Extraccion220
from tests.unit.documents.dobles import ClienteFalso, ClienteQueRevienta

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
    lector = registry.reader_for("CERT_INGRESOS_220")
    # Es un lector ATADO: el registry le fija el contexto del caso y devuelve algo que se
    # llama con `(content)`, como cualquier parser determinístico.
    assert lector.func is certificados.leer_220  # type: ignore[union-attr]


def test_el_registry_le_ata_al_lector_el_anio_del_caso():
    """El año no puede quedarse en el camino: el error más común es subir el certificado
    del año equivocado, y el guard que lo ataja necesita saber qué año se espera."""
    lector = registry.reader_for("CERT_INGRESOS_220", anio_esperado=2025)
    assert lector.keywords["anio_esperado"] == 2025  # type: ignore[union-attr]


def test_a_un_lector_deterministico_no_se_le_ata_nada():
    """Los parsers del portal no reciben contexto: leen una celda y no dependen del caso."""
    assert registry.reader_for("EXOGENA", anio_esperado=2025) is exogena.parse


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


def test_una_falla_de_infraestructura_del_modelo_es_reintentable_y_no_dice_ilegible():
    """Sin `ANTHROPIC_API_KEY` el SDK revienta con un `TypeError` al hacer la request. No es
    `ValueError`, así que sin esta traducción sube hasta el manejador genérico: 500
    `INTERNAL_ERROR` con `retryable: false`, y el documento queda guardado sin lectura y sin
    alerta. El documento no tiene nada malo y reintentar sí sirve, así que ni el código ni la
    reintentabilidad pueden ser los de un documento ilegible."""
    cliente = ClienteQueRevienta(TypeError("Could not resolve authentication method"))
    with pytest.raises(DocumentReaderUnavailableError) as exc:
        certificados.leer_220(b"%PDF-x", client=cliente)
    assert exc.value.retryable
    # Ni el texto del SDK ni el nombre de la excepción viajan al mensaje.
    assert "authentication" not in exc.value.message
    assert "TypeError" not in exc.value.message


def test_un_archivo_que_no_es_un_pdf_se_reporta_ilegible_sin_llamar_al_modelo():
    cliente = ClienteFalso(EXTRACCION)
    with pytest.raises(DocumentUnreadableError):
        certificados.leer_220(b"soy un JPG cualquiera", client=cliente)
    assert cliente.messages.llamadas == []  # el pre-flight del extractor sigue vigente


def test_el_certificado_del_anio_equivocado_no_se_lee():
    """El guard del año del extractor, ejercitado por donde ahora pasa de verdad."""
    with pytest.raises(DocumentUnreadableError):
        certificados.leer_220(b"%PDF-x", anio_esperado=2024, client=ClienteFalso(EXTRACCION))


def test_la_lectura_lleva_el_anio_gravable_del_certificado():
    """La segunda red: cuando nadie ató un año esperado, el año que dice el certificado es
    el único dato con el que un conciliador puede detectar el desfase después."""
    lectura = certificados.leer_220(b"%PDF-x", client=ClienteFalso(EXTRACCION))
    assert lectura.field("anio_gravable") == 2025


def test_el_certificado_tiene_nombre_legible_para_las_alertas():
    """La alerta del expediente dice "No se pudo leer {nombre}": ahi no cabe un código."""
    assert document_label("CERT_INGRESOS_220").startswith("el certificado")


# ─────── el año tambien tiene que entrar a la clave de la cache ───────


def _lector_que_anota(anios: list[int | None]):
    def lector(content: bytes, *, anio_esperado: int | None = None, client: object = None):
        anios.append(anio_esperado)
        return DocumentReading(doc_type="CERT_INGRESOS_220", parser="falso", content_sha256="abc")

    return lector


def test_la_cache_no_le_sirve_a_un_anio_la_lectura_de_otro(monkeypatch):
    """Sin el año en la clave, la lectura buena del año A se le sirve al año B y el guard
    del año se salta por la caché: el mismo agujero, con un paso más."""
    anios: list[int | None] = []
    monkeypatch.setitem(registry.LLM_READERS, "CERT_INGRESOS_220", _lector_que_anota(anios))
    service = DocumentReaderService()

    service.read(content=b"%PDF-x", doc_type="CERT_INGRESOS_220", anio_esperado=2025)
    service.read(content=b"%PDF-x", doc_type="CERT_INGRESOS_220", anio_esperado=2024)
    assert anios == [2025, 2024]

    # Y el mismo año sí se sirve de la caché: no se rompió lo que ya funcionaba.
    service.read(content=b"%PDF-x", doc_type="CERT_INGRESOS_220", anio_esperado=2024)
    assert anios == [2025, 2024]
