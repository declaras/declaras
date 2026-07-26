import hashlib

import pytest

from declaras.extraccion.f220 import Extraccion220, extraer_220

PDF = b"%PDF-fake"


class _RespuestaFalsa:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class _MessagesFalso:
    def __init__(self, parsed, stop_reason="end_turn"):
        self._parsed = parsed
        self._stop_reason = stop_reason
        self.llamadas = []

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        return _RespuestaFalsa(self._parsed, self._stop_reason)


class ClienteFalso:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.messages = _MessagesFalso(parsed, stop_reason)


# El total impreso reconcilia: 120 + 2 + 1 + 0 = 123M.
EXTRACCION = Extraccion220(
    empleador_nit="900123456", empleador_nombre="ACME SAS",
    anio_gravable=2025, numero_de_certificados=1,
    total_ingresos_brutos=123_000_000,
    salarios=120_000_000, cesantias_e_intereses=2_000_000, prima=1_000_000,
    bonificaciones=0, aportes_salud=4_800_000, aportes_pension=4_800_000,
    retencion=8_000_000, confianza=0.97,
)


def test_mapea_extraccion_a_ingreso_laboral():
    cliente = ClienteFalso(EXTRACCION)
    lab = extraer_220(PDF, client=cliente)
    assert lab.salarios == 120_000_000
    assert lab.bruto == 123_000_000
    assert lab.fuente.clase == "documento"
    assert lab.fuente.confianza == 0.97
    assert lab.fuente.detalle == "220"


def test_envia_pdf_como_documento_base64():
    cliente = ClienteFalso(EXTRACCION)
    extraer_220(PDF, client=cliente)
    llamada = cliente.messages.llamadas[0]
    contenido = llamada["messages"][0]["content"]
    assert contenido[0]["type"] == "document"
    assert contenido[0]["source"]["media_type"] == "application/pdf"
    assert llamada["output_format"] is Extraccion220
    assert llamada["model"] == "claude-opus-5"
    # Ancla contra regresión de truncado: thinking y respuesta comparten el presupuesto.
    assert llamada["max_tokens"] >= 16000
    # Extracción mecánica: effort bajo a propósito, no por descuido.
    assert llamada["output_config"] == {"effort": "medium"}


# Un valor DISTINTO por campo: si el constructor cruza dos campos (p. ej. salud con
# pensión), el assert del campo cruzado falla. Con montos repetidos el mutante sobrevive.
EXTRACCION_DISTINTA = Extraccion220(
    empleador_nit="800555111", empleador_nombre="OTRA LTDA",
    anio_gravable=2025, numero_de_certificados=1,
    total_ingresos_brutos=10_000_000,  # 1 + 2 + 3 + 4
    salarios=1_000_000, cesantias_e_intereses=2_000_000, prima=3_000_000,
    bonificaciones=4_000_000, aportes_salud=5_000_000, aportes_pension=6_000_000,
    retencion=7_000_000, confianza=0.5,
)


def test_mapea_cada_campo_a_su_homonimo():
    lab = extraer_220(PDF, client=ClienteFalso(EXTRACCION_DISTINTA))
    assert lab.empleador_nit == "800555111"
    assert lab.empleador_nombre == "OTRA LTDA"
    assert lab.salarios == 1_000_000
    assert lab.cesantias_e_intereses == 2_000_000
    assert lab.prima == 3_000_000
    assert lab.bonificaciones == 4_000_000
    assert lab.aportes_salud == 5_000_000
    assert lab.aportes_pension == 6_000_000
    assert lab.retencion == 7_000_000
    assert lab.bruto == 10_000_000  # 1 + 2 + 3 + 4, no arrastra aportes ni retención
    assert lab.fuente.confianza == 0.5


def test_fuente_ref_es_el_hash_del_pdf():
    lab = extraer_220(PDF, client=ClienteFalso(EXTRACCION))
    assert lab.fuente.ref == hashlib.sha256(PDF).hexdigest()[:12]
    assert len(lab.fuente.ref) == 12


def test_falla_con_error_de_dominio_si_no_hay_salida_estructurada():
    cliente = ClienteFalso(None, stop_reason="refusal")
    with pytest.raises(ValueError, match="no produjo salida estructurada") as exc:
        extraer_220(PDF, client=cliente)
    assert "refusal" in str(exc.value)  # el stop_reason llega al mensaje


# --- guards ruidosos: el extractor alimenta un formulario tributario ---

def test_rechaza_bytes_que_no_son_pdf_sin_llamar_al_api():
    cliente = ClienteFalso(EXTRACCION)
    with pytest.raises(ValueError, match="no parece un PDF"):
        extraer_220(b"soy un JPG cualquiera", client=cliente)
    assert cliente.messages.llamadas == []  # pre-flight: no gasta una llamada


def test_rechaza_pdf_con_varios_certificados():
    ext = EXTRACCION.model_copy(update={"numero_de_certificados": 2})
    with pytest.raises(ValueError, match="2 certificados") as exc:
        extraer_220(PDF, client=ClienteFalso(ext))
    assert "uno a la vez" in str(exc.value)


def test_rechaza_certificado_con_pensiones():
    ext = EXTRACCION.model_copy(update={"pensiones_de_jubilacion": 30_000_000})
    with pytest.raises(ValueError, match="IngresoPension"):
        extraer_220(PDF, client=ClienteFalso(ext))


def test_rechaza_extraccion_que_no_reconcilia_contra_el_total():
    # El total impreso dice 123M pero los campos suman 100M: se perdieron 23M.
    ext = EXTRACCION.model_copy(update={"salarios": 97_000_000})
    with pytest.raises(ValueError, match="no reconcilia") as exc:
        extraer_220(PDF, client=ClienteFalso(ext))
    mensaje = str(exc.value)
    assert "100,000,000" in mensaje and "123,000,000" in mensaje  # ambas cifras


def test_tolera_diferencia_de_redondeo_hasta_mil_pesos():
    ext = EXTRACCION.model_copy(update={"total_ingresos_brutos": 123_000_999})
    lab = extraer_220(PDF, client=ClienteFalso(ext))
    assert lab.bruto == 123_000_000  # pasa: 999 <= 1000


def test_rechaza_certificado_de_otro_anio_gravable():
    with pytest.raises(ValueError, match="2024") as exc:
        extraer_220(PDF, anio_esperado=2024, client=ClienteFalso(EXTRACCION))
    assert "2025" in str(exc.value)  # el año del certificado también


def test_acepta_el_anio_esperado_correcto():
    lab = extraer_220(PDF, anio_esperado=2025, client=ClienteFalso(EXTRACCION))
    assert lab.salarios == 120_000_000


def test_nit_invalido_no_pasa_el_schema():
    with pytest.raises(ValueError):  # pydantic ValidationError hereda de ValueError
        Extraccion220(
            empleador_nit="900.123.456-7", empleador_nombre="ACME SAS",
            anio_gravable=2025, numero_de_certificados=1,
            total_ingresos_brutos=1, salarios=1,
            aportes_salud=0, aportes_pension=0, confianza=0.9,
        )
