from declaras.caso import CasoTributario
from declaras.motor.cierre import cerrar, validar
from declaras.motor.elecciones import Elecciones
from declaras.motor.general import base_general, rlg_general
from declaras.motor.impuesto import impuesto_total
from declaras.motor.pensiones import rlg_pensiones
from declaras.motor.traza import Liquidacion, Traza
from declaras.parametros import ParametrosAnio


def liquidar(caso: CasoTributario, p: ParametrosAnio, elecciones: Elecciones) -> Liquidacion:
    """Función pura: Caso + Parámetros + Elecciones → Liquidación trazable."""
    if caso.anio_gravable != p.anio:
        raise ValueError(
            f"El caso es del año gravable {caso.anio_gravable} pero los parámetros "
            f"son del {p.anio}: liquidar con UVT y topes de otro año daría cifras malas"
        )
    t = Traza()
    base_general(caso, p, t)
    rg = rlg_general(caso, p, elecciones, t)
    rp = rlg_pensiones(caso, p, t)
    imp = impuesto_total(caso, p, t, rg, rp)
    cerrar(caso, p, t, imp)
    validar(caso, p, t)
    return t.a_liquidacion(caso.anio_gravable, elecciones)
