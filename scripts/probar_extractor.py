"""Prueba manual de un extractor contra el API real.

Es la única verificación que las pruebas NO pueden hacer: todas usan un doble del cliente, así
que confirman los guards y el mapeo pero no que la llamada al proveedor tenga la forma correcta.
Hasta que esto corra una vez con una llave de verdad, el contrato con el proveedor es una
suposición leída de la documentación.

Uso:
    export GEMINI_API_KEY=...
    uv run python scripts/probar_extractor.py ruta/al/220.pdf [anio_esperado]
    uv run python scripts/probar_extractor.py --tipo pension ruta/al/certificado.pdf 2025

Tipos: 220 (por defecto), pension, bancario, dividendos, arriendo, beneficio.
"""

import os
import sys
from pathlib import Path

from declaras.extraccion.cert_arriendo import extraer_arriendo
from declaras.extraccion.cert_bancario import extraer_bancario
from declaras.extraccion.cert_beneficio import extraer_beneficio
from declaras.extraccion.cert_dividendos import extraer_dividendos
from declaras.extraccion.cert_pension import extraer_pension
from declaras.extraccion.f220 import extraer_220

EXTRACTORES = {
    "220": extraer_220,
    "pension": extraer_pension,
    "bancario": extraer_bancario,
    "dividendos": extraer_dividendos,
    "arriendo": extraer_arriendo,
    "beneficio": extraer_beneficio,
}

argumentos = sys.argv[1:]
tipo = "220"
if argumentos and argumentos[0] == "--tipo":
    if len(argumentos) < 2 or argumentos[1] not in EXTRACTORES:
        sys.exit(f"Tipos: {', '.join(EXTRACTORES)}")
    tipo = argumentos[1]
    argumentos = argumentos[2:]

if not 1 <= len(argumentos) <= 2:
    sys.exit(__doc__.strip())

if not os.getenv("GEMINI_API_KEY"):
    sys.exit("Falta GEMINI_API_KEY. Sin llave no hay nada que probar contra el API real.")

anio_esperado = int(argumentos[1]) if len(argumentos) == 2 else None
resultado = EXTRACTORES[tipo](Path(argumentos[0]).read_bytes(), anio_esperado)

# El bancario devuelve dos cosas (el rendimiento y el GMF) y el de beneficio devuelve el tipo
# leído más el monto: se imprime lo que haya, sin asumir una sola forma.
for parte in resultado if isinstance(resultado, tuple) else (resultado,):
    if parte is None:
        print("(sin dato)")
    elif hasattr(parte, "model_dump_json"):
        print(parte.model_dump_json(indent=2))
    else:
        print(parte)
