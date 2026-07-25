from pathlib import Path

import yaml

from declaras.parametros.modelos import ParametrosAnio, Tramo

__all__ = ["ParametrosAnio", "Tramo", "cargar"]

_DIR = Path(__file__).parent


def cargar(anio: int) -> ParametrosAnio:
    ruta = _DIR / f"ag{anio}.yaml"
    if not ruta.exists():
        raise ValueError(f"No hay parámetros para el año gravable {anio}")
    return ParametrosAnio.model_validate(yaml.safe_load(ruta.read_text()))
