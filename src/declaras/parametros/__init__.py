from pathlib import Path

import yaml

from declaras.parametros.modelos import ParametrosAnio, Tramo

__all__ = ["ParametrosAnio", "Tramo", "cargar"]

_DIR = Path(__file__).parent


def cargar(anio: int) -> ParametrosAnio:
    ruta = _DIR / f"ag{anio}.yaml"
    if not ruta.exists():
        raise ValueError(f"No hay parámetros para el año gravable {anio}")
    p = ParametrosAnio.model_validate(
        yaml.safe_load(ruta.read_text(encoding="utf-8")))
    if p.anio != anio:
        raise ValueError(f"ag{anio}.yaml declara anio={p.anio}; el archivo no corresponde")
    return p
