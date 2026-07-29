from pathlib import Path

import pytest

from declaras import parametros
from declaras.dinero import pesos, porcentaje
from declaras.parametros import ParametrosAnio, cargar
from declaras.parametros.tabla import impuesto_tabla_241


def _tramos_ag2025() -> list[dict]:
    return [t.model_dump() for t in cargar(2025).tabla_241]


def _ag2025_con_tramos(tramos: list[dict]) -> dict:
    """Datos de AG 2025 con la tabla 241 reemplazada, para ejercitar las guardas."""
    datos = cargar(2025).model_dump()
    datos["tabla_241"] = tramos
    return datos


def test_pesos_half_up():
    assert pesos(1495976.78) == 1495977
    assert pesos(373994.25) == 373994
    assert pesos(0.5) == 1
    assert pesos(10) == 10


def test_porcentaje_exacto_en_la_frontera():
    """Multiplica en Decimal, así que el producto que cae en ,50 sube de verdad.

    Los dos casos con 0,35 son los que discriminan: con `pesos(monto * tarifa)`
    (float primero) darían un peso menos, porque 0,35 no es exacto en binario y el
    producto aterriza en ...,4999. Con 0,19 y 0,25 el float coincide, pero los dejo
    como cobertura de la frontera ,50 por si cambia la tarifa del YAML.
    """
    assert porcentaje(89_844_110, 0.35) == 31_445_439  # exacto 31.445.438,50 → sube
    assert porcentaje(90, 0.35) == 32  # exacto 31,50 → sube
    assert porcentaje(50, 0.19) == 10  # exacto 9,50 → sube
    assert porcentaje(2, 0.25) == 1  # exacto 0,50 → sube
    # Casos sin frontera: coincide con la aritmética obvia.
    assert porcentaje(10_000_000, 0.35) == 3_500_000
    assert porcentaje(25_719_090, 0.19) == 4_886_627
    assert porcentaje(0, 0.35) == 0


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
    assert impuesto_tabla_241(54_280_910, p) == 0  # exacto en 1.090 UVT
    assert impuesto_tabla_241(62_154_472, p) == 1_495_977  # tramo 19% (constante 0)
    assert impuesto_tabla_241(125_212_000, p) == 17_131_720  # tramo 28% + 116 UVT
    assert impuesto_tabla_241(118_978_944, p) == 15_386_464


def test_cargar_rechaza_yaml_de_otro_anio(tmp_path, monkeypatch):
    origen = Path(parametros.__file__).parent / "ag2025.yaml"
    (tmp_path / "ag2030.yaml").write_text(origen.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(parametros, "_DIR", tmp_path)
    with pytest.raises(ValueError, match="no corresponde"):
        parametros.cargar(2030)


def test_cargar_anio_sin_parametros():
    with pytest.raises(ValueError, match="No hay parámetros"):
        cargar(1999)


def test_clave_desconocida_en_el_yaml_revienta(tmp_path, monkeypatch):
    # `extra="forbid"` en los parámetros: un parámetro con typo (o renombrado en el YAML
    # de un año nuevo) se descartaba en silencio y el motor liquidaba con la regla vieja.
    origen = Path(parametros.__file__).parent / "ag2025.yaml"
    texto = origen.read_text(encoding="utf-8") + "\nlimite_general_porcentaje: 0.45\n"
    (tmp_path / "ag2025.yaml").write_text(texto, encoding="utf-8")
    monkeypatch.setattr(parametros, "_DIR", tmp_path)
    with pytest.raises(ValueError, match="limite_general_porcentaje"):
        parametros.cargar(2025)


def test_clave_desconocida_en_un_tramo_revienta():
    tramos = _tramos_ag2025()
    tramos[2]["constante"] = 116  # el campo real es constante_uvt
    with pytest.raises(ValueError, match=r"tabla_241\.2\.constante"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tramo_sin_constante_uvt_revienta():
    # Sin default: un tramo que omite la constante no es "constante 0". En el tramo del
    # 28% eso subestimaría el impuesto en 116 UVT (5.776.684 pesos) sin avisar.
    tramos = _tramos_ag2025()
    del tramos[2]["constante_uvt"]
    with pytest.raises(ValueError, match="constante_uvt"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tabla_241_debe_empezar_en_cero():
    tramos = _tramos_ag2025()[1:]  # arranca en 1.090 UVT: se pierde el tramo exento
    with pytest.raises(ValueError, match="desde_uvt=0"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tabla_241_desordenada_revienta():
    tramos = _tramos_ag2025()
    tramos[1], tramos[2] = tramos[2], tramos[1]  # 19% y 28% invertidos
    with pytest.raises(ValueError, match="contiguos"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tabla_241_con_hueco_revienta():
    tramos = _tramos_ag2025()
    tramos[2]["desde_uvt"] = 1800  # deja 1.700..1.800 UVT sin gravar
    with pytest.raises(ValueError, match="contiguos"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tabla_241_solo_el_ultimo_tramo_es_abierto():
    tramos = _tramos_ag2025()
    tramos[1]["hasta_uvt"] = None
    with pytest.raises(ValueError, match="último tramo"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))


def test_tabla_241_ultimo_tramo_debe_ser_abierto():
    tramos = _tramos_ag2025()
    tramos[-1]["hasta_uvt"] = 40_000  # dejaría sin gravar lo que supere 40.000 UVT
    with pytest.raises(ValueError, match="debe ser abierto"):
        ParametrosAnio.model_validate(_ag2025_con_tramos(tramos))
