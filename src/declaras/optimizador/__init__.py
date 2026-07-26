from pydantic import BaseModel, ConfigDict

from declaras.caso import CasoTributario
from declaras.motor import Elecciones, Liquidacion, liquidar
from declaras.parametros import ParametrosAnio

__all__ = ["ResultadoOptimizacion", "ahorro_marginal", "optimizar"]


class ResultadoOptimizacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liquidacion: Liquidacion
    elecciones: Elecciones
    evaluadas: int


def _combos(caso: CasoTributario) -> list[Elecciones]:
    if not caso.beneficios.dependientes:
        return [Elecciones(usar_387=False, usar_72uvt=False)]
    return [Elecciones(usar_387=a, usar_72uvt=b)
            for a in (False, True) for b in (False, True)]


def optimizar(caso: CasoTributario, p: ParametrosAnio) -> ResultadoOptimizacion:
    """Enumera las elecciones legales, evalúa el motor y elige la de menor impuesto.

    Desempate determinista: menos elecciones activas, luego orden de la tupla.
    """
    evaluados = [(liquidar(caso, p, e), e) for e in _combos(caso)]
    liq, e = min(
        evaluados,
        key=lambda par: (par[0].valor("IMPUESTO_NETO"), par[1].activas,
                         (par[1].usar_387, par[1].usar_72uvt)),
    )
    return ResultadoOptimizacion(liquidacion=liq, elecciones=e,
                                 evaluadas=len(evaluados))


def ahorro_marginal(caso_base: CasoTributario, caso_con_hecho: CasoTributario,
                    p: ParametrosAnio) -> int:
    """Cuánto impuesto ahorra un hecho: base del 'cada pregunta lleva su ahorro'."""
    return (optimizar(caso_base, p).liquidacion.valor("IMPUESTO_NETO")
            - optimizar(caso_con_hecho, p).liquidacion.valor("IMPUESTO_NETO"))
