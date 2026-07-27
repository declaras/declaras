from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from declaras.caso import CasoTributario
from declaras.motor import Elecciones, Flag, Liquidacion, liquidar
from declaras.parametros import ParametrosAnio

__all__ = ["ResultadoOptimizacion", "ahorro_marginal", "optimizar"]

# Severidad de `Flag` que impide optimizar. Literal del motor (`traza.Flag.severidad`).
_BLOQUEANTE = "bloqueante"


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


def optimizar(caso: CasoTributario, p: ParametrosAnio, *,
              flags_previos: Sequence[Flag] = ()) -> ResultadoOptimizacion:
    """Enumera las elecciones legales, evalúa el motor y elige la de menor impuesto.

    Desempate determinista: menos elecciones activas, luego orden de la tupla.

    NO OPTIMIZA SOBRE UNA LIQUIDACIÓN BLOQUEADA. Un aviso `bloqueante` dice que a esta
    base le falta algo (hoy: un ingreso que el motor no liquida y que el contador tiene
    que sumar a mano), y la elección de menor impuesto sobre una base incompleta puede ser
    la equivocada para el 210 completo — el contador se quedaría con la elección mala
    justo cuando ya nadie va a recalcular. Antes esto era una etiqueta que el render
    pintaba y que acá nadie miraba.

    Se revisan las dos fuentes: los flags que levanta el motor al liquidar y los
    `flags_previos` de quien llama (los avisos del conciliador, que el motor no puede
    levantar porque está congelado y `CasoTributario` no tiene dónde llevarlos). Cubrir
    solo una dejaría la otra abierta.
    """
    evaluados = [(liquidar(caso, p, e), e) for e in _combos(caso)]
    _exigir_sin_bloqueantes([*flags_previos,
                             *(f for liq, _ in evaluados for f in liq.flags)])
    liq, e = min(
        evaluados,
        key=lambda par: (par[0].valor("IMPUESTO_NETO"), par[1].activas,
                         (par[1].usar_387, par[1].usar_72uvt)),
    )
    return ResultadoOptimizacion(liquidacion=liq, elecciones=e,
                                 evaluadas=len(evaluados))


def _exigir_sin_bloqueantes(flags: Sequence[Flag]) -> None:
    codigos = sorted({f.codigo for f in flags if f.severidad == _BLOQUEANTE})
    if codigos:
        raise ValueError(
            f"No se optimiza una liquidación con alertas bloqueantes "
            f"({', '.join(codigos)}): la elección de menor impuesto sobre una base "
            "incompleta puede ser la equivocada para el 210 completo. Hay que resolver "
            "esas alertas antes de elegir."
        )


def ahorro_marginal(caso_base: CasoTributario, caso_con_hecho: CasoTributario,
                    p: ParametrosAnio, *,
                    flags_previos: Sequence[Flag] = ()) -> int:
    """Cuánto impuesto ahorra un hecho: base del 'cada pregunta lleva su ahorro'.

    Los ahorros marginales NO son aditivos: para mostrar ahorro por pregunta
    acumulado, calcular cada uno sobre el caso ya acumulado, no todos contra el
    mismo base.

    `flags_previos` viaja a las DOS optimizaciones. Era la puerta paralela del
    bloqueo: con un aviso bloqueante vivo esto seguía optimizando dos veces y
    devolvía un ahorro calculado sobre una base incompleta, que es exactamente
    la promesa que el bloqueo existe para no hacer.
    """
    if (caso_base.contribuyente.num_doc != caso_con_hecho.contribuyente.num_doc
            or caso_base.anio_gravable != caso_con_hecho.anio_gravable):
        raise ValueError("ahorro_marginal compara dos versiones del MISMO caso")
    return (optimizar(caso_base, p,
                      flags_previos=flags_previos).liquidacion.valor("IMPUESTO_NETO")
            - optimizar(caso_con_hecho, p,
                        flags_previos=flags_previos).liquidacion.valor("IMPUESTO_NETO"))
