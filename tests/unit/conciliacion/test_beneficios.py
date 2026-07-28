"""De la lectura de un certificado de beneficio al caso que se liquida.

Es el eslabón que faltaba: los cinco extractores de beneficios producían lecturas y nada las
llevaba a `Beneficios`, así que prepagada, vivienda, ICETEX, AFC, donaciones y el 4x1000 se
leían y no se declaraban.
"""

from __future__ import annotations

from declaras.documents.models import DocumentReading, ExtractedField
from declaras.services.conciliacion.beneficios import beneficios_de

TOPES = 999  # no se usa; el tope lo aplica el motor, acá solo se transporta el hecho


def _lectura(doc_type: str, **campos) -> DocumentReading:
    return DocumentReading(
        doc_type=doc_type,
        parser="test",
        content_sha256="a" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.9) for k, v in campos.items()],
    )


def _prepagada(valor: int = 4_800_000, nit: str = "900222333") -> DocumentReading:
    return _lectura(
        "CERT_PREPAGADA",
        tipo_beneficio="PREPAGADA",
        entidad_nit=nit,
        entidad_nombre="Aseguradora Demo",
        valor=valor,
        certificada=True,
        anio_gravable=2025,
    )


def test_cada_certificado_aterriza_en_su_casilla_del_caso():
    """Un valor distinto por beneficio: si el mapeo cruzara dos casillas, el tope que el
    motor aplica sería el de otro beneficio y el impuesto saldría mal."""
    lecturas = [
        _prepagada(),
        _lectura(
            "CERT_INTERESES_VIVIENDA",
            tipo_beneficio="INTERESES_VIVIENDA",
            entidad_nit="890903938",
            entidad_nombre="Banco Demo",
            valor=18_500_000,
            certificada=True,
            anio_gravable=2025,
        ),
        _lectura(
            "CERT_ICETEX",
            tipo_beneficio="ICETEX",
            entidad_nit="899999241",
            entidad_nombre="ICETEX",
            valor=2_100_000,
            certificada=True,
            anio_gravable=2025,
        ),
        _lectura(
            "CERT_AFC_FVP",
            tipo_beneficio="AFC_FVP",
            entidad_nit="800111222",
            entidad_nombre="Fondo Demo",
            valor=12_000_000,
            certificada=True,
            anio_gravable=2025,
        ),
        _lectura(
            "CERT_DONACION_ESAL",
            tipo_beneficio="DONACION_ESAL",
            entidad_nit="800333444",
            entidad_nombre="Fundación Demo",
            valor=3_300_000,
            certificada=True,
            anio_gravable=2025,
        ),
    ]
    beneficios, avisos = beneficios_de(lecturas)

    assert beneficios.medicina_prepagada is not None
    assert beneficios.medicina_prepagada.valor == 4_800_000
    assert beneficios.intereses_vivienda is not None
    assert beneficios.intereses_vivienda.valor == 18_500_000
    assert beneficios.intereses_icetex is not None
    assert beneficios.intereses_icetex.valor == 2_100_000
    assert [a.valor for a in beneficios.aportes_afc_fvp] == [12_000_000]
    assert [d.valor for d in beneficios.donaciones_esal] == [3_300_000]
    assert avisos == []


def test_el_gmf_del_certificado_bancario_llega_a_beneficios():
    """El 4x1000 viene dentro del certificado del banco, que además trae un INGRESO. El
    ingreso lo cruza el conciliador; el GMF no tiene contraparte en la exógena y este es el
    único camino por el que llega al 210."""
    bancario = _lectura(
        "CERT_BANCARIO",
        entidad_nit="890903938",
        entidad_nombre="Bancolombia",
        rendimientos=3_456_789,
        retencion=241_975,
        gmf_pagado=87_654,
        anio_gravable=2025,
    )
    beneficios, _ = beneficios_de([bancario])
    assert beneficios.gmf_pagado is not None
    assert beneficios.gmf_pagado.valor == 87_654


def test_un_bancario_sin_gmf_no_inventa_el_beneficio():
    bancario = _lectura(
        "CERT_BANCARIO",
        entidad_nit="890903938",
        entidad_nombre="Bancolombia",
        rendimientos=3_456_789,
        gmf_pagado=0,
        anio_gravable=2025,
    )
    beneficios, _ = beneficios_de([bancario])
    assert beneficios.gmf_pagado is None


def test_dos_certificados_del_mismo_beneficio_y_entidades_distintas_suman_y_lo_avisan():
    """Dos aseguradoras son dos pagos legítimos. Suman, pero el aviso lo dice: el contador
    tiene que poder ver de qué se compone la cifra que va al renglón."""
    beneficios, avisos = beneficios_de(
        [_prepagada(4_800_000, "900222333"), _prepagada(1_200_000, "900555666")]
    )
    assert beneficios.medicina_prepagada is not None
    assert beneficios.medicina_prepagada.valor == 6_000_000
    assert [a.codigo for a in avisos] == ["BENEFICIO_DE_VARIOS_CERTIFICADOS"]
    assert "6,000,000" in avisos[0].mensaje


def test_el_mismo_certificado_dos_veces_no_duplica_el_beneficio():
    """Misma entidad, mismo tipo y mismo valor es el MISMO certificado otra vez —un
    re-escaneo llega con otro hash, así que el sha no lo detecta—. Sumarlo doblaría una
    deducción en silencio, que es el error que más plata mueve en este proyecto."""
    beneficios, avisos = beneficios_de([_prepagada(), _prepagada()])
    assert beneficios.medicina_prepagada is not None
    assert beneficios.medicina_prepagada.valor == 4_800_000
    assert [a.codigo for a in avisos] == ["CERTIFICADO_REPETIDO"]


def test_un_certificado_sin_soporte_formal_no_entra_y_se_avisa():
    """El extractor ya rechaza una captura de pantalla, así que esto es la segunda red: si
    una lectura vieja o de otro camino dice `certificada=False`, no se declara callado."""
    lectura = _prepagada()
    lectura = lectura.model_copy(
        update={
            "fields": [
                f if f.name != "certificada" else f.model_copy(update={"value": False})
                for f in lectura.fields
            ]
        }
    )
    beneficios, avisos = beneficios_de([lectura])
    assert beneficios.medicina_prepagada is None
    assert [a.codigo for a in avisos] == ["BENEFICIO_SIN_SOPORTE"]
    assert avisos[0].severidad == "advertencia"


def test_las_lecturas_que_no_son_de_beneficio_se_ignoran_sin_ruido():
    """El expediente trae la exógena, el RUT y el 220. Ninguno aporta beneficios y ninguno
    debe producir un aviso: un aviso por documento normal ensucia la lista y le quita
    autoridad a los que sí hay que atender."""
    beneficios, avisos = beneficios_de(
        [_lectura("EXOGENA"), _lectura("RUT"), _lectura("CERT_INGRESOS_220", salarios=1)]
    )
    assert beneficios.medicina_prepagada is None
    assert avisos == []


def test_un_tipo_de_beneficio_nuevo_sin_casilla_revienta():
    """La partición tiene que ser total, igual que en el ensamble del caso y en el catálogo
    de peticiones: un beneficio nuevo sin casilla dejaría de declararse EN SILENCIO."""
    import pytest

    from declaras.services.conciliacion.beneficios import _CASILLA_POR_TIPO

    guardado = dict(_CASILLA_POR_TIPO)
    _CASILLA_POR_TIPO.pop("PREPAGADA")
    try:
        with pytest.raises(NotImplementedError, match="PREPAGADA"):
            beneficios_de([_prepagada()])
    finally:
        _CASILLA_POR_TIPO.clear()
        _CASILLA_POR_TIPO.update(guardado)
