from declaras.extraccion.f220 import Extraccion220, extraer_220


class _RespuestaFalsa:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _MessagesFalso:
    def __init__(self, parsed):
        self._parsed = parsed
        self.llamadas = []

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        return _RespuestaFalsa(self._parsed)


class ClienteFalso:
    def __init__(self, parsed):
        self.messages = _MessagesFalso(parsed)


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
