"""La liquidación conciliada: la ÚNICA puerta por la que las partidas se vuelven un 210.

DOS COSAS PASAN ACÁ Y NINGUNA ES COSMÉTICA.

1. LA FUSIÓN DE AVISOS. `conciliacion.avisos(partidas)` es el único canal por el que los
   SIETE códigos del conciliador llegan impresos al borrador: el motor está congelado y no
   puede levantarlos, y `CasoTributario` no tiene dónde llevarlos. Uno es BLOQUEANTE —"este
   ingreso quedó por fuera de la liquidación", con el tercero, el concepto y la cifra para
   que el contador lo sume a mano—, y sin la fusión un 210 incompleto NO SE VE incompleto.
   Por eso la fusión vive dentro de `liquidar_conciliado` y no en el llamador: un llamador
   que se la olvide es un 210 que miente, y no puede depender de que alguien recuerde un
   segundo paso.

2. QUE `bloqueante` BLOQUEE DE VERDAD. Era una etiqueta que solo se pintaba: el render la
   imprimía y el optimizador elegía elecciones sin mirarla. Bloqueante significa: la
   liquidación se puede VER (el borrador es justamente donde el contador lee qué le falta)
   pero NO se optimiza y NO se cierra. No se optimiza porque la elección de menor impuesto
   calculada sobre una base a la que le falta un ingreso puede ser la equivocada para el
   210 completo, y el contador que suma ese ingreso a mano se quedaría con la elección
   mala: se liquida con las elecciones POR DEFECTO del modelo, que son las declaradas y no
   las "mejores" de una base incompleta. No se cierra: eso lo exige el servicio antes de
   marcar el borrador listo, con `bloqueantes()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import Field

from declaras.caso import CasoTributario
from declaras.motor import Elecciones, Flag, Liquidacion, liquidar
from declaras.optimizador import optimizar
from declaras.parametros import ParametrosAnio, cargar
from declaras.services.conciliacion.mapeo import avisos
from declaras.services.conciliacion.modelos import Partida, _Modelo

# La severidad de `Flag` que bloquea. Literal del motor (`traza.Flag.severidad`), con
# nombre acá para que el chequeo no quede repartido en cadenas sueltas por tres capas.
SEVERIDAD_BLOQUEANTE: Final = "bloqueante"

# El nodo que mide "cuánto impuesto", y el que mide "cuánto se paga". La ganancia del
# producto es la del impuesto (contrato del plan); el saldo viaja al lado porque es lo que
# el cliente siente —la retención no baja el impuesto, baja lo que le toca girar—.
CODIGO_IMPUESTO: Final = "IMPUESTO_NETO"
CODIGO_SALDO: Final = "SALDO"


class LiquidacionVersionada(_Modelo):
    """Una liquidación con el momento en que se calculó.

    Se guarda TAL CUAL y no se recalcula al leerla: el preliminar es la foto de lo que se
    sabía antes de que llegara un solo documento del cliente, y recalcularlo con los datos
    de hoy borraría la ganancia que el producto existe para mostrar.
    """

    version: int = Field(ge=1)
    momento: datetime
    liquidacion: Liquidacion
    # ¿Esta versión se liquidó SIN ningún documento del cliente, o sea solo con lo que la
    # DIAN reporta? Es lo que hace que la ganancia signifique "lo que el trabajo con los
    # documentos le ahorró". El preliminar tiene que serlo, y cuando no se pudo (el
    # expediente ya traía documentos cruzables y el caso sin ellos no se podía armar) la
    # ganancia subestima y quien la muestre tiene que poder decirlo. Es estructural y no
    # inferido de `version == 1`: la versión 1 puede no ser el preliminar puro.
    base_sin_documentos: bool = False

    @property
    def impuesto(self) -> int:
        return self.liquidacion.valor(CODIGO_IMPUESTO)

    @property
    def saldo(self) -> int:
        return self.liquidacion.valor(CODIGO_SALDO)


def hay_bloqueante(flags: Sequence[Flag]) -> bool:
    """¿Alguno de estos avisos impide dar la liquidación por buena?"""
    return any(f.severidad == SEVERIDAD_BLOQUEANTE for f in flags)


def bloqueantes(liquidacion: Liquidacion) -> list[Flag]:
    """Los avisos bloqueantes vivos de una liquidación, para poder enumerarlos.

    Enumerar y no contar: quien reciba el 409 tiene que poder leer QUÉ lo bloquea, o el
    bloqueo es una puerta cerrada sin letrero.
    """
    return [f for f in liquidacion.flags if f.severidad == SEVERIDAD_BLOQUEANTE]


def liquidar_conciliado(
    caso: CasoTributario, partidas: Sequence[Partida], p: ParametrosAnio
) -> Liquidacion:
    """El 210 de un caso conciliado: optimizado si se puede, con los avisos fusionados.

    El orden importa: los avisos se calculan ANTES de elegir elecciones, porque de ellos
    depende si se optimiza. La segunda guarda (`flags_previos` al optimizador) es
    deliberadamente redundante con la primera: si alguien cambia esta función y se salta
    el `if`, el optimizador se sigue negando en vez de devolver un óptimo de una base
    incompleta.
    """
    extra = avisos(list(partidas))
    # La liquidación con las elecciones por defecto sirve para dos cosas: es la que se
    # publica cuando hay bloqueante, y es la que revela un bloqueante levantado por el
    # MOTOR (hoy no emite ninguno, pero el camino tiene que estar cubierto: la lección de
    # esta rama es que cada guard cubre el camino que el autor tenía en la cabeza).
    por_defecto = liquidar(caso, p, Elecciones())
    if hay_bloqueante([*por_defecto.flags, *extra]):
        base = por_defecto
    else:
        base = optimizar(caso, p, flags_previos=extra).liquidacion
    return base.model_copy(update={"flags": [*base.flags, *extra]})


def liquidar_y_versionar(
    caso: CasoTributario,
    partidas: Sequence[Partida],
    *,
    p: ParametrosAnio | None = None,
    version: int = 1,
    momento: datetime | None = None,
    base_sin_documentos: bool = False,
) -> LiquidacionVersionada:
    """Liquida y rotula el resultado con su versión y su momento.

    Pura: quién la guarda y con qué número de versión lo decide el repositorio. `p` se
    puede inyectar; por defecto salen los parámetros del año gravable del caso.
    """
    parametros = p if p is not None else cargar(caso.anio_gravable)
    return LiquidacionVersionada(
        version=version,
        momento=momento if momento is not None else datetime.now(tz=UTC),
        liquidacion=liquidar_conciliado(caso, partidas, parametros),
        base_sin_documentos=base_sin_documentos,
    )


def ganancia(preliminar: LiquidacionVersionada, actual: LiquidacionVersionada) -> int:
    """Cuánto bajó el impuesto entre el preliminar y la liquidación de hoy.

    Positiva = el trabajo con los documentos del cliente le bajó el impuesto. Puede ser
    NEGATIVA y eso no es un error que tapar: un certificado que muestra un ingreso que la
    exógena no tenía sube el impuesto, y esconderlo sería mentirle al cliente sobre lo que
    va a pagar.
    """
    return preliminar.impuesto - actual.impuesto


__all__ = [
    "CODIGO_IMPUESTO",
    "CODIGO_SALDO",
    "SEVERIDAD_BLOQUEANTE",
    "LiquidacionVersionada",
    "bloqueantes",
    "ganancia",
    "hay_bloqueante",
    "liquidar_conciliado",
    "liquidar_y_versionar",
]
