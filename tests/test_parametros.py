from declaras.dinero import pesos
from declaras.parametros import cargar
from declaras.parametros.tabla import impuesto_tabla_241


def test_pesos_half_up():
    assert pesos(1495976.78) == 1495977
    assert pesos(373994.25) == 373994
    assert pesos(0.5) == 1
    assert pesos(10) == 10


def test_carga_ag2025():
    p = cargar(2025)
    assert p.uvt == 49799
    assert p.uvt_pesos(1340) == 66_730_660
    assert p.uvt_pesos(1090) == 54_280_910
    assert p.uvt_pesos(790) == 39_341_210
    assert p.componente_inflacionario is None  # pendiente decreto


def test_tabla_241():
    p = cargar(2025)
    assert impuesto_tabla_241(0, p) == 0
    assert impuesto_tabla_241(54_280_910, p) == 0            # exacto en 1.090 UVT
    assert impuesto_tabla_241(62_154_472, p) == 1_495_977    # tramo 19%
    assert impuesto_tabla_241(125_212_000, p) == 17_126_740  # tramo 28%
    assert impuesto_tabla_241(118_978_944, p) == 15_381_484
