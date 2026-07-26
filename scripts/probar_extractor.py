"""Prueba manual del extractor 220 contra el API real.

Uso: uv run python scripts/probar_extractor.py ruta/al/220.pdf [anio_esperado]
Requiere ANTHROPIC_API_KEY o perfil de `ant auth login`.
"""
import sys
from pathlib import Path

from declaras.extraccion import extraer_220

if not 2 <= len(sys.argv) <= 3:
    sys.exit(__doc__.strip())

anio_esperado = int(sys.argv[2]) if len(sys.argv) == 3 else None
lab = extraer_220(Path(sys.argv[1]).read_bytes(), anio_esperado=anio_esperado)
print(lab.model_dump_json(indent=2))
