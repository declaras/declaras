from pathlib import Path

import yaml

from declaras.parametros.modelos import ParametrosAnio, Tramo

__all__ = ["UVT_POR_ANIO", "ParametrosAnio", "Tramo", "cargar", "uvt_de"]

_DIR = Path(__file__).parent

# Valor de la UVT por año gravable, en pesos. Conviven dos en cualquier momento: la del año
# que se declara (para sus topes) y la del año en curso (sanciones, planeación), así que la
# UVT nunca puede ser una sola constante. Esta es la ÚNICA tabla del proyecto: `tax/uvt.py`
# delega acá y el YAML de cada año se valida contra ella. Con dos tablas, la que nadie
# recuerda actualizar liquida con la UVT del año equivocado sin avisar.
UVT_POR_ANIO: dict[int, int] = {
    2019: 34_270,
    2020: 35_607,
    2021: 36_308,
    2022: 38_004,
    2023: 42_412,
    2024: 47_065,
    2025: 49_799,
    2026: 52_374,
}


def uvt_de(anio: int) -> int:
    """UVT del año gravable indicado."""
    valor = UVT_POR_ANIO.get(anio)
    if valor is None:
        raise ValueError(
            f"No hay UVT registrada para el año {anio}; disponibles: {sorted(UVT_POR_ANIO)}"
        )
    return valor


def cargar(anio: int) -> ParametrosAnio:
    ruta = _DIR / f"ag{anio}.yaml"
    if not ruta.exists():
        raise ValueError(f"No hay parámetros para el año gravable {anio}")
    p = ParametrosAnio.model_validate(yaml.safe_load(ruta.read_text(encoding="utf-8")))
    if p.anio != anio:
        raise ValueError(f"ag{anio}.yaml declara anio={p.anio}; el archivo no corresponde")
    return p
