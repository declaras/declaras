import pytest

from declaras.extraccion.f220 import Extraccion220, extraer_220


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


EXTRACCION = Extraccion220(
    empleador_nit="900123456", empleador_nombre="ACME SAS",
    salarios=120_000_000, cesantias_e_intereses=2_000_000, prima=1_000_000,
    bonificaciones=0, aportes_salud=4_800_000, aportes_pension=4_800_000,
    retencion=8_000_000, confianza=0.97,
)


def test_mapea_extraccion_a_ingreso_laboral():
    cliente = ClienteFalso(EXTRACCION)
    lab = extraer_220(b"%PDF-fake", client=cliente)
    assert lab.salarios == 120_000_000
    assert lab.bruto == 123_000_000
    assert lab.fuente.clase == "documento"
    assert lab.fuente.confianza == 0.97
    assert lab.fuente.detalle == "220"


def test_envia_pdf_como_documento_base64():
    cliente = ClienteFalso(EXTRACCION)
    extraer_220(b"%PDF-fake", client=cliente)
    llamada = cliente.messages.llamadas[0]
    contenido = llamada["messages"][0]["content"]
    assert contenido[0]["type"] == "document"
    assert contenido[0]["source"]["media_type"] == "application/pdf"
    assert llamada["output_format"] is Extraccion220


# Un valor DISTINTO por campo: si el constructor cruza dos campos (p. ej. salud con
# pensión), el assert del campo cruzado falla. Con montos repetidos el mutante sobrevive.
EXTRACCION_DISTINTA = Extraccion220(
    empleador_nit="800555111", empleador_nombre="OTRA LTDA",
    salarios=1_000_000, cesantias_e_intereses=2_000_000, prima=3_000_000,
    bonificaciones=4_000_000, aportes_salud=5_000_000, aportes_pension=6_000_000,
    retencion=7_000_000, confianza=0.5,
)


def test_mapea_cada_campo_a_su_homonimo():
    lab = extraer_220(b"%PDF-fake", client=ClienteFalso(EXTRACCION_DISTINTA))
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


def test_falla_con_error_de_dominio_si_no_hay_salida_estructurada():
    cliente = ClienteFalso(None, stop_reason="refusal")
    with pytest.raises(ValueError, match="no produjo salida estructurada") as exc:
        extraer_220(b"%PDF-fake", client=cliente)
    assert "refusal" in str(exc.value)  # el stop_reason llega al mensaje
