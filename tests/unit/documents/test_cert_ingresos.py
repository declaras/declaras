"""Los cuatro extractores de certificados de ingreso: pensión, bancario, dividendos, arriendo.

Cada caso usa VALORES DISTINTOS POR CAMPO a propósito. Un mapeo cruzado —la retención en el
canon, el predial en la comisión— pasa cualquier prueba que reuse la misma cifra en dos campos,
y ese cruce es exactamente el error que un extractor comete: son diez campos con nombres
parecidos leídos por un modelo.
"""

from __future__ import annotations

import pytest

from declaras.documents import registry
from declaras.extraccion.cert_arriendo import (
    ExtraccionArriendo,
    MotivoArriendo,
    extraer_arriendo_con_metadatos,
)
from declaras.extraccion.cert_bancario import (
    ExtraccionBancario,
    MotivoBancario,
    extraer_bancario,
)
from declaras.extraccion.cert_dividendos import (
    ExtraccionDividendos,
    MotivoDividendos,
    extraer_dividendos,
)
from declaras.extraccion.cert_pension import (
    ExtraccionPension,
    MotivoPension,
    extraer_pension,
)
from tests.unit.documents.dobles import ClienteFalso

PDF = b"%PDF-1.7 fixture"


# ───────────────────────────────── pensión ─────────────────────────────────

PENSION = ExtraccionPension(
    pagador_nit="900123456",
    pagador_nombre="Colpensiones",
    anio_gravable=2025,
    # Doce valores DISTINTOS: si el extractor promediara el total o repitiera una mesada,
    # la lista dejaría de ser reconocible. Diciembre lleva la mesada 13 sumada.
    mesadas=[
        4_000_000,
        4_100_000,
        4_200_000,
        4_300_000,
        4_400_000,
        4_500_000,
        4_600_000,
        4_700_000,
        4_800_000,
        4_900_000,
        5_000_000,
        9_000_000,
    ],
    total_pagado=58_500_000,
    retencion=1_234_567,
    confianza=0.93,
)


def test_pension_mapea_campo_por_campo():
    p = extraer_pension(PDF, anio_esperado=2025, client=ClienteFalso(PENSION))
    assert p.pagador == "Colpensiones"
    assert p.pagador_nit == "900123456"  # sin NIT la partida no cruza contra la exógena
    assert p.mesadas == list(PENSION.mesadas)
    assert p.retencion == 1_234_567
    assert p.fuente.confianza == pytest.approx(0.93)


def test_pension_conserva_las_mesadas_mes_a_mes():
    """La exención pensional es de 1.000 UVT POR MES, así que doce mesadas distintas y su
    promedio no dan el mismo impuesto: repartir el total a ojo cambia la cifra declarada."""
    p = extraer_pension(PDF, anio_esperado=2025, client=ClienteFalso(PENSION))
    assert p.mesadas[11] == 9_000_000
    assert len(set(p.mesadas)) == 12
    assert sum(p.mesadas) == PENSION.total_pagado


def test_pension_que_no_reconcilia_contra_el_total_impreso_falla():
    malo = PENSION.model_copy(update={"total_pagado": 70_000_000})
    with pytest.raises(ValueError, match="reconcilia") as exc:
        extraer_pension(PDF, client=ClienteFalso(malo))
    assert exc.value.motivo is MotivoPension.NO_RECONCILIA


def test_pension_con_mesadas_que_no_son_doce_falla():
    malo = PENSION.model_copy(update={"mesadas": [5_000_000] * 11, "total_pagado": 55_000_000})
    with pytest.raises(ValueError, match="doce") as exc:
        extraer_pension(PDF, client=ClienteFalso(malo))
    assert exc.value.motivo is MotivoPension.MESADAS_INCOMPLETAS


# ───────────────────────────────── bancario ─────────────────────────────────

BANCARIO = ExtraccionBancario(
    entidad_nit="890903938",
    entidad_nombre="Bancolombia",
    anio_gravable=2025,
    rendimientos=3_456_789,
    retencion=241_975,
    gmf_pagado=87_654,
    saldo_31_dic=52_000_000,
    numero_de_cuentas=1,
    confianza=0.9,
)


def test_bancario_mapea_y_separa_el_gmf_de_los_rendimientos():
    """El GMF no es ingreso: es el 4x1000 pagado, y va a beneficios. Meterlo en
    `rendimientos` inventaría ingreso y perdería la deducción a la vez."""
    rend, gmf = extraer_bancario(PDF, anio_esperado=2025, client=ClienteFalso(BANCARIO))
    assert rend.entidad == "Bancolombia"
    assert rend.entidad_nit == "890903938"
    assert rend.valor == 3_456_789
    assert rend.retencion == 241_975
    assert gmf is not None
    assert gmf.valor == 87_654
    assert gmf.fuente.ref == rend.fuente.ref  # el mismo documento respalda los dos hechos


def test_bancario_sin_gmf_no_inventa_el_beneficio():
    rend, gmf = extraer_bancario(
        PDF, client=ClienteFalso(BANCARIO.model_copy(update={"gmf_pagado": 0}))
    )
    assert rend.valor == 3_456_789
    assert gmf is None


def test_bancario_con_varias_cuentas_suma_y_lo_dice_en_la_confianza():
    """Un certificado que agrega tres cuentas es una cifra correcta con menos trazabilidad:
    nadie puede verificar cuenta por cuenta. Se suma, y la confianza lo declara."""
    varias = BANCARIO.model_copy(update={"numero_de_cuentas": 3})
    rend, _ = extraer_bancario(PDF, client=ClienteFalso(varias))
    una, _ = extraer_bancario(PDF, client=ClienteFalso(BANCARIO))
    assert rend.valor == una.valor
    assert rend.fuente.confianza is not None and una.fuente.confianza is not None
    assert rend.fuente.confianza < una.fuente.confianza


def test_bancario_sin_cuentas_falla():
    with pytest.raises(ValueError) as exc:
        extraer_bancario(
            PDF, client=ClienteFalso(BANCARIO.model_copy(update={"numero_de_cuentas": 0}))
        )
    assert exc.value.motivo is MotivoBancario.SIN_CUENTAS


# ──────────────────────────────── dividendos ────────────────────────────────

DIVIDENDOS = ExtraccionDividendos(
    sociedad_nit="900777888",
    sociedad_nombre="Inversiones Demo SAS",
    anio_gravable=2025,
    anio_utilidades=2024,
    gravados=12_345_678,
    no_gravados=7_654_321,
    total_distribuido=19_999_999,
    retencion=1_481_481,
    discrimina=True,
    confianza=0.88,
)


def test_dividendos_mapea_gravados_y_no_gravados_por_separado():
    d = extraer_dividendos(PDF, anio_esperado=2025, client=ClienteFalso(DIVIDENDOS))
    assert d.sociedad_nit == "900777888"
    assert d.gravados == 12_345_678
    assert d.no_gravados == 7_654_321
    assert d.retencion == 1_481_481


def test_dividendos_sin_discriminar_falla_en_vez_de_adivinar():
    """Los gravados llevan la tarifa del artículo 240 más el 242; los no gravados solo la
    tabla. Partir el total a ojo cambia el impuesto, así que sin la discriminación no se
    liquida: se pide el certificado completo."""
    sin = DIVIDENDOS.model_copy(update={"discrimina": False, "gravados": 0, "no_gravados": 0})
    with pytest.raises(ValueError, match="discrimina") as exc:
        extraer_dividendos(PDF, client=ClienteFalso(sin))
    assert exc.value.motivo is MotivoDividendos.NO_DISCRIMINA


def test_dividendos_que_no_reconcilian_contra_el_total_fallan():
    malo = DIVIDENDOS.model_copy(update={"total_distribuido": 30_000_000})
    with pytest.raises(ValueError, match="reconcilia") as exc:
        extraer_dividendos(PDF, client=ClienteFalso(malo))
    assert exc.value.motivo is MotivoDividendos.NO_RECONCILIA


# ───────────────────────────────── arriendo ─────────────────────────────────

ARRIENDO = ExtraccionArriendo(
    inmueble="Apto 501 Calle 100 #15-20",
    contraparte_nombre="Inmobiliaria Demo SAS",
    contraparte_nit="900555666",
    anio_gravable=2025,
    canon_total=36_000_000,
    meses=12,
    retencion=1_260_000,
    predial=1_800_000,
    administracion=2_400_000,
    comision_inmobiliaria=3_600_000,
    reparaciones=950_000,
    confianza=0.85,
)


def test_arriendo_mapea_cada_costo_a_su_casilla():
    a, _, _ = extraer_arriendo_con_metadatos(PDF, anio_esperado=2025, client=ClienteFalso(ARRIENDO))
    assert a.inmueble == "Apto 501 Calle 100 #15-20"
    assert a.contraparte_nombre == "Inmobiliaria Demo SAS"
    assert a.contraparte_nit == "900555666"
    assert a.canon_total == 36_000_000
    assert a.retencion == 1_260_000
    assert a.costos.predial == 1_800_000
    assert a.costos.administracion == 2_400_000
    assert a.costos.comision_inmobiliaria == 3_600_000
    assert a.costos.reparaciones == 950_000


def test_arriendo_sin_canon_falla():
    with pytest.raises(ValueError) as exc:
        extraer_arriendo_con_metadatos(
            PDF, client=ClienteFalso(ARRIENDO.model_copy(update={"canon_total": 0}))
        )
    assert exc.value.motivo is MotivoArriendo.CANON_VACIO


def test_arriendo_con_costos_mayores_al_canon_avisa_pero_no_falla():
    """Costos por encima del canon dan renta negativa. Puede ser cierto (una reparación
    grande en un año flojo) y puede ser un costo leído mal, así que no se rechaza: se
    declara para revisión y la decisión es de una persona."""
    caro = ARRIENDO.model_copy(update={"reparaciones": 40_000_000})
    a, aviso, _ = extraer_arriendo_con_metadatos(PDF, client=ClienteFalso(caro))
    assert a.costos.total > a.canon_total
    assert aviso is not None
    assert aviso.needs_action is True


def test_arriendo_normal_no_deja_aviso():
    _, aviso, _ = extraer_arriendo_con_metadatos(PDF, client=ClienteFalso(ARRIENDO))
    assert aviso is None


# ─────────────────────────── registro y cruce ───────────────────────────

TIPOS = ("CERT_PENSION", "CERT_BANCARIO", "CERT_DIVIDENDOS", "CERT_ARRIENDO")


@pytest.mark.parametrize("doc_type", TIPOS)
def test_los_cuatro_tipos_estan_registrados_como_familia_con_modelo(doc_type):
    assert doc_type in registry.supported_types()
    assert registry.is_deterministic(doc_type) is False
    assert registry.reader_for(doc_type, anio_esperado=2025) is not None


@pytest.mark.parametrize("doc_type", TIPOS)
def test_los_cuatro_tipos_saben_cruzarse(doc_type):
    """Registrar el lector no alcanza: el conciliador cruza por `TIPO_A_CLAVE`, y un
    certificado con lector pero sin clave se queda en la bandeja y su petición no se cierra."""
    from declaras.services.conciliacion.cruce import TIPO_A_CLAVE

    assert doc_type in TIPO_A_CLAVE
    assert TIPO_A_CLAVE[doc_type]


# ──────────── el contrato entre lo que se pide y lo que se sabe leer ────────────

# Tipos que el catálogo pide y que TODAVÍA nadie sabe leer: el contador los recibe y los lleva
# a mano. Están enumerados y no tolerados en silencio, que es la diferencia entre una deuda
# conocida y un documento que entra al expediente y no cierra su petición nunca.
SIN_LECTOR_TODAVIA = frozenset(
    {
        # No es un certificado: es la prueba de un dependiente (registro civil, cédula del padre).
        "SOPORTE_DEPENDIENTE",
        # El GMF viene dentro del certificado bancario, pero se pide aparte porque alguien puede
        # haber pagado 4x1000 sin tener rendimientos que la DIAN reporte, y entonces no hay
        # partida de rendimientos que dispare la petición del bancario.
        "CERT_GMF",
        # La certificación del salario promedio de los últimos seis meses (art. 206 num. 4) no
        # tiene formato: la escribe Gestión Humana en papel membreteado y cada empresa la redacta
        # distinta. No hay parser determinista posible, así que el dato lo captura una persona.
        # Es la única entrada de la exención de cesantías, y por eso está declarada acá y no
        # tolerada en silencio: el día que haya un formato estándar, esta línea lo recuerda.
        "CERT_PROMEDIO_CESANTIAS",
    }
)


def test_todo_documento_que_el_catalogo_pide_se_sabe_leer_o_esta_declarado():
    """El `tipo_documento` de una petición y la llave del registry son EL MISMO string.

    Si divergen, el cliente manda el archivo correcto, el sistema no lo reconoce y la
    petición no se cierra nunca — y nadie se entera, porque las dos tablas se leen bien por
    separado. Ya pasó: el catálogo pedía `CERT_RENDIMIENTOS` y `CERT_ARRENDAMIENTO` mientras
    los lectores se registraron como `CERT_BANCARIO` y `CERT_ARRIENDO`, y la petición de
    pensión pedía un 220, que es justo el documento que el lector del 220 rechaza cuando
    trae pensiones.
    """
    from declaras.services.conciliacion.peticiones import (
        _CERTIFICADO_POR_CONCEPTO,
        BENEFICIOS,
    )

    pedidos = {b.tipo_documento for b in BENEFICIOS} | {
        c.tipo_documento for c in _CERTIFICADO_POR_CONCEPTO.values()
    }
    sin_lector = pedidos - set(registry.supported_types()) - SIN_LECTOR_TODAVIA
    assert not sin_lector, (
        f"El catálogo pide {sorted(sin_lector)} y nadie sabe leerlos: esos documentos entran "
        "al expediente y su petición no se cierra. Registra el lector o decláralo en "
        "SIN_LECTOR_TODAVIA."
    )
