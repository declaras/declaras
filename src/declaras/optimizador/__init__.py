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
    """Cuánto impuesto ahorra un hecho: base del 'cada pregunta lleva su ahorro'.

    Los ahorros marginales NO son aditivos: para mostrar ahorro por pregunta
    acumulado, calcular cada uno sobre el caso ya acumulado, no todos contra el
    mismo base.
    """
    if (caso_base.contribuyente.num_doc != caso_con_hecho.contribuyente.num_doc
            or caso_base.anio_gravable != caso_con_hecho.anio_gravable):
        raise ValueError("ahorro_marginal compara dos versiones del MISMO caso")
    return (optimizar(caso_base, p).liquidacion.valor("IMPUESTO_NETO")
            - optimizar(caso_con_hecho, p).liquidacion.valor("IMPUESTO_NETO"))
