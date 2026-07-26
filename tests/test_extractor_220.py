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


def _kwargs_validos(**cambios):
    """Kwargs de un 220 laboral que reconcilia: 120 + 2 + 1 + 0 + 0 = 123M.

    Se construye por kwargs (no con `model_copy`) donde el test necesite ejercitar la
    validación del schema: `model_copy(update=…)` la salta.
    """
    base = dict(
        empleador_nit="900123456", empleador_nombre="ACME SAS",
        anio_gravable=2025, numero_de_certificados=1,
        total_ingresos_brutos=123_000_000,
        salarios=120_000_000, cesantias_e_intereses=2_000_000, prima=1_000_000,
        bonificaciones=0, pensiones_de_jubilacion=0,
        aportes_salud=4_800_000, aportes_pension=4_800_000,
        retencion=8_000_000, confianza=0.97,
    )
    base.update(cambios)
    return base


EXTRACCION = Extraccion220(**_kwargs_validos())


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
    # Extracción mecánica: effort medium a propósito, no por descuido.
    assert llamada["output_config"] == {"effort": "medium"}


# Un valor DISTINTO por campo: si el constructor cruza dos campos (p. ej. salud con
# pensión), el assert del campo cruzado falla. Con montos repetidos el mutante sobrevive.
EXTRACCION_DISTINTA = Extraccion220(**_kwargs_validos(
    empleador_nit="800555111", empleador_nombre="OTRA LTDA",
    total_ingresos_brutos=10_000_000,  # 1 + 2 + 3 + 4
    salarios=1_000_000, cesantias_e_intereses=2_000_000, prima=3_000_000,
    bonificaciones=4_000_000, aportes_salud=5_000_000, aportes_pension=6_000_000,
    retencion=7_000_000, confianza=0.5,
))


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


# El 220 mixto es el caso que se colaba en silencio: un modelo obediente plegaba las
# pensiones dentro de `bonificaciones` para cumplir la regla de partición, la
# reconciliación cuadraba y el ingreso pensional se liquidaba como laboral.
MIXTO = Extraccion220(**_kwargs_validos(
    pensiones_de_jubilacion=30_000_000,
    total_ingresos_brutos=153_000_000,  # el total impreso INCLUYE las pensiones
))


def test_mixto_dispara_el_guard_de_pensiones_y_no_el_de_reconciliacion():
    with pytest.raises(ValueError, match="IngresoPension") as exc:
        extraer_220(PDF, client=ClienteFalso(MIXTO))
    mensaje = str(exc.value)
    assert "no reconcilia" not in mensaje  # el total con pensiones SÍ reconcilia
    assert "30,000,000" in mensaje  # dice cuánta pensión encontró


def test_pensiones_de_jubilacion_es_obligatoria_en_el_schema():
    # Sin default: el modelo debe declararla siempre, aunque sea 0. Si fuera opcional,
    # un modelo que la omite deja pasar el 220 mixto como laboral puro.
    sin_pensiones = {
        k: v for k, v in _kwargs_validos().items() if k != "pensiones_de_jubilacion"
    }
    with pytest.raises(ValueError, match="pensiones_de_jubilacion"):
        Extraccion220(**sin_pensiones)


def test_rechaza_extraccion_que_no_reconcilia_contra_el_total():
    # El total impreso dice 123M pero los campos suman 100M: se perdieron 23M.
    ext = EXTRACCION.model_copy(update={"salarios": 97_000_000})
    with pytest.raises(ValueError, match="no reconcilia") as exc:
        extraer_220(PDF, client=ClienteFalso(ext))
    mensaje = str(exc.value)
    assert "100,000,000" in mensaje and "123,000,000" in mensaje  # ambas cifras


@pytest.mark.parametrize("diferencia, revienta", [(1_000, False), (1_001, True)])
def test_borde_exacto_de_la_tolerancia(diferencia, revienta):
    # Ancla del borde: 1000 pasa, 1001 revienta. Mata los mutantes `1_000 -> 999`
    # y `> -> >=`, que solo se distinguen justo en el borde.
    ext = EXTRACCION.model_copy(
        update={"total_ingresos_brutos": 123_000_000 + diferencia}
    )
    if revienta:
        with pytest.raises(ValueError, match="no reconcilia"):
            extraer_220(PDF, client=ClienteFalso(ext))
    else:
        assert extraer_220(PDF, client=ClienteFalso(ext)).bruto == 123_000_000


def test_rechaza_certificado_de_otro_anio_gravable():
    with pytest.raises(ValueError, match="2024") as exc:
        extraer_220(PDF, anio_esperado=2024, client=ClienteFalso(EXTRACCION))
    assert "2025" in str(exc.value)  # el año del certificado también


def test_el_anio_se_verifica_antes_de_la_reconciliacion():
    # Fixture que viola AMBOS: año equivocado y total que no cuadra. Gana el año, que es
    # identidad del documento ("subiste el 220 del año equivocado") y no puede
    # falso-rechazar; la reconciliación es calidad de extracción.
    ext = EXTRACCION.model_copy(update={"salarios": 97_000_000})
    with pytest.raises(ValueError, match="año gravable") as exc:
        extraer_220(PDF, anio_esperado=2024, client=ClienteFalso(ext))
    assert "no reconcilia" not in str(exc.value)


def test_acepta_el_anio_esperado_correcto():
    lab = extraer_220(PDF, anio_esperado=2025, client=ClienteFalso(EXTRACCION))
    assert lab.salarios == 120_000_000


# 7-8 dígitos: NIT de persona natural (cédula), legítimo y masivo como empleador.
@pytest.mark.parametrize("nit", ["1234567", "12345678", "900123456", "9001234567"])
def test_acepta_nit_de_siete_a_diez_digitos(nit):
    assert Extraccion220(**_kwargs_validos(empleador_nit=nit)).empleador_nit == nit


@pytest.mark.parametrize(
    "nit", ["123456", "12345678901", "900.123.456-7", "900123456-7", "abc123456", ""]
)
def test_rechaza_nit_mal_formado(nit):
    with pytest.raises(ValueError):  # pydantic ValidationError hereda de ValueError
        Extraccion220(**_kwargs_validos(empleador_nit=nit))
