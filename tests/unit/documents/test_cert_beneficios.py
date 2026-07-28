"""Los cinco certificados de beneficios y la autodetección del tipo de documento."""

from __future__ import annotations

import pytest

from declaras.documents import registry
from declaras.documents.sniff import DESCONOCIDO, detectar_tipo
from declaras.extraccion.cert_beneficio import (
    DOC_TYPE_POR_TIPO,
    ExtraccionBeneficio,
    MotivoBeneficio,
    TipoBeneficio,
    extraer_beneficio,
)
from tests.unit.documents.dobles import ClienteFalso

PDF = b"%PDF-1.7 fixture"

# Un valor distinto por tipo: si el lector despachara al tipo equivocado, la cifra lo delata.
VALORES = {
    TipoBeneficio.PREPAGADA: 4_800_000,
    TipoBeneficio.INTERESES_VIVIENDA: 18_500_000,
    TipoBeneficio.ICETEX: 2_100_000,
    TipoBeneficio.AFC_FVP: 12_000_000,
    TipoBeneficio.DONACION_ESAL: 3_300_000,
}


def _extraccion(tipo: TipoBeneficio, **cambios) -> ExtraccionBeneficio:
    base = ExtraccionBeneficio(
        tipo=tipo,
        entidad=f"Entidad de {tipo.value}",
        entidad_nit="900222333",
        valor=VALORES[tipo],
        anio_gravable=2025,
        certificada=True,
        confianza=0.91,
    )
    return base.model_copy(update=cambios) if cambios else base


@pytest.mark.parametrize("tipo", list(TipoBeneficio))
def test_cada_beneficio_mapea_su_valor_su_anio_y_su_confianza(tipo):
    leido, monto = extraer_beneficio(
        PDF, tipo=tipo, anio_esperado=2025, client=ClienteFalso(_extraccion(tipo))
    )
    assert leido is tipo
    assert monto.valor == VALORES[tipo]
    assert monto.fuente.confianza == pytest.approx(0.91)


def test_sin_hint_el_modelo_clasifica_y_lo_reporta():
    """La petición no siempre sabe qué mandó el cliente: sin hint, el tipo lo dice el
    documento y quien llama se entera de cuál fue."""
    leido, monto = extraer_beneficio(
        PDF, client=ClienteFalso(_extraccion(TipoBeneficio.ICETEX))
    )
    assert leido is TipoBeneficio.ICETEX
    assert monto.valor == VALORES[TipoBeneficio.ICETEX]


def test_el_hint_no_silencia_la_discrepancia():
    """Se pidió prepagada y llegó el del ICETEX. Gana el DOCUMENTO: el caso probable no es
    que el modelo confunda cinco encabezados muy distintos, es que la persona subió el
    archivo en la casilla equivocada — y aceptar el hint metería la cifra bajo otro tope."""
    with pytest.raises(ValueError, match="ICETEX") as exc:
        extraer_beneficio(
            PDF,
            tipo=TipoBeneficio.PREPAGADA,
            client=ClienteFalso(_extraccion(TipoBeneficio.ICETEX)),
        )
    assert exc.value.motivo is MotivoBeneficio.TIPO_QUE_NO_COINCIDE


def test_un_certificado_en_cero_no_se_acepta():
    """Registrar 0 dejaría la petición cerrada con el beneficio perdido, que es peor que
    volver a pedirlo: lo más probable es que el modelo no encontró la cifra."""
    with pytest.raises(ValueError) as exc:
        extraer_beneficio(
            PDF, client=ClienteFalso(_extraccion(TipoBeneficio.PREPAGADA, valor=0))
        )
    assert exc.value.motivo is MotivoBeneficio.SIN_VALOR


def test_una_captura_de_pantalla_no_sirve_de_soporte():
    """Un beneficio soportado en un extracto es el que hay que devolver con intereses si la
    DIAN revisa dentro de los tres años de firmeza."""
    with pytest.raises(ValueError) as exc:
        extraer_beneficio(
            PDF, client=ClienteFalso(_extraccion(TipoBeneficio.AFC_FVP, certificada=False))
        )
    assert exc.value.motivo is MotivoBeneficio.SIN_CERTIFICAR


@pytest.mark.parametrize("tipo", list(TipoBeneficio))
def test_el_lector_del_registry_valida_que_sea_el_beneficio_que_se_pidio(tipo):
    """Cada lector lleva su `doc_type` esperado cerrado adentro, así que despachar el
    certificado equivocado a la casilla equivocada falla en la frontera."""
    from declaras.domain.errors import DocumentUnreadableError

    doc_type = DOC_TYPE_POR_TIPO[tipo]
    lector = registry.reader_for(doc_type, anio_esperado=2025)
    assert lector is not None

    otro = next(t for t in TipoBeneficio if t is not tipo)
    with pytest.raises(DocumentUnreadableError):
        lector(PDF, client=ClienteFalso(_extraccion(otro)))  # type: ignore[call-arg]


def test_el_lector_produce_la_lectura_con_el_tipo_y_el_valor():
    lector = registry.reader_for("CERT_PREPAGADA", anio_esperado=2025)
    assert lector is not None
    lectura = lector(  # type: ignore[call-arg]
        PDF, client=ClienteFalso(_extraccion(TipoBeneficio.PREPAGADA))
    )
    assert lectura.doc_type == "CERT_PREPAGADA"
    assert lectura.field("valor") == VALORES[TipoBeneficio.PREPAGADA]
    assert lectura.field("tipo_beneficio") == "PREPAGADA"
    assert lectura.field("certificada") is True
    # El digest completo, como en las dos familias: un cruce lectura↔documento por hash no
    # puede fallar solo para esta.
    assert len(lectura.content_sha256) == 64


# ──────────────────────────── autodetección ────────────────────────────


class _Clasif:
    """Lo que el modelo devuelve al clasificar. No importa la clase, importan los campos."""

    def __init__(self, doc_type: str, confianza: float = 0.95) -> None:
        self.doc_type = doc_type
        self.confianza = confianza


def test_detectar_reconoce_un_tipo_soportado():
    assert detectar_tipo(PDF, client=ClienteFalso(_Clasif("CERT_INGRESOS_220"))) == (
        "CERT_INGRESOS_220"
    )


def test_detectar_devuelve_desconocido_para_basura():
    assert detectar_tipo(PDF, client=ClienteFalso(_Clasif(DESCONOCIDO))) == DESCONOCIDO


def test_una_etiqueta_inventada_es_desconocido_y_no_un_tipo():
    """Si el modelo responde algo que no está en el registry, quien llama tiene que
    preguntar: un `doc_type` inventado despacharía al lector equivocado o a ninguno."""
    assert detectar_tipo(PDF, client=ClienteFalso(_Clasif("CERT_LO_QUE_SEA"))) == DESCONOCIDO


def test_una_clasificacion_dudosa_es_desconocido():
    """Preguntar cuesta una pregunta; clasificar mal pone la cifra en el renglón equivocado
    del formulario. Por debajo del umbral no se adivina."""
    assert detectar_tipo(PDF, client=ClienteFalso(_Clasif("CERT_ICETEX", 0.4))) == DESCONOCIDO
    assert detectar_tipo(PDF, client=ClienteFalso(_Clasif("CERT_ICETEX", 0.95))) == "CERT_ICETEX"


def test_el_prompt_de_clasificacion_describe_todos_los_tipos_registrados():
    """Un tipo registrado que el prompt no describa es un tipo que la autodetección nunca va
    a proponer, en silencio: el modelo no puede elegir una etiqueta que no vio."""
    from declaras.documents.sniff import _DESCRIPCIONES

    faltan = set(registry.supported_types()) - set(_DESCRIPCIONES)
    assert not faltan, f"sin descripción para clasificar: {sorted(faltan)}"
