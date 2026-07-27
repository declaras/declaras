import pytest
from pydantic import ValidationError

from declaras.caso import (
    CasoTributario, Contribuyente, Creditos, Fuente, IngresoLaboral, IngresoPension,
    MontoDeclarado, Patrimonio,
)

FX = Fuente.fixture("test")


def _laboral(**kw):
    base = dict(
        empleador_nit="900123456", empleador_nombre="ACME SAS",
        salarios=120_000_000, aportes_salud=4_800_000,
        aportes_pension=4_800_000, retencion=8_000_000, fuente=FX,
    )
    base.update(kw)
    return IngresoLaboral(**base)


def test_bruto_laboral_suma_componentes():
    lab = _laboral(cesantias_e_intereses=2_000_000, prima=1_000_000)
    assert lab.bruto == 123_000_000


def test_pension_exige_12_mesadas():
    with pytest.raises(ValidationError):
        IngresoPension(pagador="Colpensiones", mesadas=[10_000_000] * 11, fuente=FX)


def test_montos_no_negativos():
    with pytest.raises(ValidationError):
        _laboral(salarios=-1)


def test_caso_minimo_e_ingresos_totales():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="1234567", nombre="Prueba"),
        laborales=[_laboral()],
    )
    assert caso.anio_gravable == 2025
    assert caso.ingresos_brutos_totales == 120_000_000


def test_monto_declarado_no_acepta_negativos():
    with pytest.raises(ValidationError):
        MontoDeclarado(valor=-5, fuente=FX)


def test_claves_desconocidas_son_rechazadas():
    """Un typo de clave debe reventar, no descartarse en silencio."""
    with pytest.raises(ValidationError):
        _laboral(salariosss=120_000_000)
    with pytest.raises(ValidationError):
        Fuente(clase="manual", ref="x", confianzaa=0.5)
    with pytest.raises(ValidationError):
        CasoTributario(
            contribuyente=Contribuyente(num_doc="1234567", nombre="Prueba"),
            laboraless=[],
        )


def test_documento_conserva_la_pagina_cero():
    assert Fuente.documento("cert_laboral", "d1", pagina=0).detalle == "cert_laboral pág 0"


@pytest.mark.parametrize("confianza", [-0.1, 1.5])
def test_confianza_fuera_de_rango(confianza):
    with pytest.raises(ValidationError):
        Fuente(clase="documento", ref="d1", confianza=confianza)


def test_confianza_admite_los_bordes_y_nulo():
    assert Fuente(clase="documento", ref="d1", confianza=0.0).confianza == 0.0
    assert Fuente(clase="documento", ref="d1", confianza=1.0).confianza == 1.0
    assert Fuente(clase="documento", ref="d1").confianza is None


def test_conciliacion_es_una_clase_de_fuente_propia():
    """Lo que resuelve el contador sobre una partida no es un dato digitado a mano.

    `ref` guarda la partida, así que desde el hecho se puede volver a la discrepancia.
    """
    f = Fuente.conciliacion("p-42", "acepta el certificado del banco")
    assert f.clase == "conciliacion"
    assert f.ref == "p-42"
    assert f.detalle == "acepta el certificado del banco"
    assert f.confianza is None


def test_celda_es_opcional_y_no_altera_las_fuentes_existentes():
    """La granularidad de la capa de documentos entra sin obligar a las demás fuentes."""
    assert Fuente.documento("exogena", "d1").celda is None
    assert Fuente.manual("contador").celda is None
    assert Fuente(clase="documento", ref="d1", celda="Hoja1!C14").celda == "Hoja1!C14"


def test_una_clase_de_fuente_inventada_revienta():
    with pytest.raises(ValidationError):
        Fuente(clase="conciliacon", ref="p-42")


def test_impuesto_neto_anterior_no_negativo():
    with pytest.raises(ValidationError):
        Creditos(impuesto_neto_anio_anterior=-1)
    assert Creditos().impuesto_neto_anio_anterior is None
    assert Creditos(impuesto_neto_anio_anterior=0).impuesto_neto_anio_anterior == 0


def test_patrimonio_liquido_anterior_admite_negativo():
    """Un patrimonio líquido negativo es legítimo (deudas > activos)."""
    patrimonio = Patrimonio(patrimonio_liquido_anterior=-5_000_000)
    assert patrimonio.patrimonio_liquido_anterior == -5_000_000
