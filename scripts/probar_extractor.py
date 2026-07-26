"""Prueba manual del extractor 220 contra el API real.

Uso: uv run python scripts/probar_extractor.py ruta/al/220.pdf
Requiere ANTHROPIC_API_KEY o perfil de `ant auth login`.
"""
import sys
from pathlib import Path

from declaras.extraccion import extraer_220

lab = extraer_220(Path(sys.argv[1]).read_bytes())
print(lab.model_dump_json(indent=2))
