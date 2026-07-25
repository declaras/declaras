from decimal import Decimal

from declaras.dinero import pesos
from declaras.parametros.modelos import ParametrosAnio


def impuesto_tabla_241(base: int, p: ParametrosAnio) -> int:
    """Impuesto marginal del art. 241 ET sobre una base en pesos."""
    if base <= 0:
        return 0
    total = Decimal(0)
    for tramo in p.tabla_241:
        desde = tramo.desde_uvt * p.uvt
        if base <= desde:
            break
        hasta = tramo.hasta_uvt * p.uvt if tramo.hasta_uvt is not None else None
        techo = min(base, hasta) if hasta is not None else base
        total += Decimal(techo - desde) * Decimal(str(tramo.tarifa))
    return pesos(total)
