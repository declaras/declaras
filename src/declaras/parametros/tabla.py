from decimal import Decimal

from declaras.dinero import pesos
from declaras.parametros.modelos import ParametrosAnio


def impuesto_tabla_241(base: int, p: ParametrosAnio) -> int:
    """Impuesto del art. 241 ET con la fórmula publicada en la ley:
    (base − límite inferior del tramo) × tarifa + constante del tramo en UVT."""
    if base <= 0:
        return 0
    for tramo in p.tabla_241:
        desde = tramo.desde_uvt * p.uvt
        hasta = tramo.hasta_uvt * p.uvt if tramo.hasta_uvt is not None else None
        if base > desde and (hasta is None or base <= hasta):
            return pesos(
                Decimal(base - desde) * Decimal(str(tramo.tarifa)) + tramo.constante_uvt * p.uvt
            )
    return 0
