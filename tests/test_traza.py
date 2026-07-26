import pytest
from pydantic import ValidationError

from declaras.motor.elecciones import Elecciones
from declaras.motor.traza import Traza


def test_nodo_registra_y_devuelve():
    t = Traza()
    v = t.nodo("A", "Ingreso", 100, "suma de x", insumos=["hecho:laboral[0]"])
    assert v == 100
    assert t.nodos["A"].formula == "suma de x"


def test_codigo_duplicado_revienta():
    """Los códigos son casilla del 210: dos módulos del motor no pueden pisarse."""
    t = Traza()
    t.nodo("A", "Ingreso", 100, "x")
    with pytest.raises(ValueError, match="duplicado"):
        t.nodo("A", "Otra cosa", 200, "y")


def test_valor_no_entero_revienta():
    """Un solo punto de redondeo: los decimales se redondean ANTES de la traza."""
    t = Traza()
    with pytest.raises(ValidationError):
        t.nodo("X", "x", 0.6, "f")


def test_liquidacion_lookup_y_flags():
    t = Traza()
    t.nodo("A", "Ingreso", 100, "x")
    t.flag("PRUEBA", "algo por revisar")
    liq = t.a_liquidacion(2025, Elecciones())
    assert liq.valor("A") == 100
    assert liq.tiene_flag("PRUEBA")
    assert not liq.tiene_flag("OTRA")
    assert liq.elecciones.usar_72uvt is True
