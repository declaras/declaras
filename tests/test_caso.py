import pytest
from pydantic import ValidationError

from declaras.caso import (
    CasoTributario, Contribuyente, Fuente, IngresoLaboral, IngresoPension,
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
