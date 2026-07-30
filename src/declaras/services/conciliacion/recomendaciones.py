"""Qué le ahorraría cada beneficio, esté o no pedido, en pesos de impuesto.

POR QUÉ NO ALCANZABA CON LAS PETICIONES. `derivar_peticiones` construye una cola de trabajo: lo que
falta pedir. Por eso descarta lo que ya se contestó y lo que ya está capturado, que es correcto para
una cola y es exactamente lo que sobra para una recomendación. Contestar "no tengo medicina
prepagada" apagaba la petición y con ella la única cifra que decía cuánto se estaba dejando en la
mesa. Quien mira después no puede distinguir "esto no aplica" de "esto se descartó sin mirar".

Acá el catálogo se recorre COMPLETO y cada beneficio sale con su estado: aplicado, disponible o
descartado. El cálculo del ahorro es el mismo (`_ahorro` de `peticiones`), no una segunda
implementación: dos cifras de ahorro que se calculan aparte terminan diciendo cosas distintas para
el mismo beneficio, y la que se ve primero gana.

LA CIFRA ES IMPUESTO QUE SE DEJA DE PAGAR, NO REDUCCIÓN DE LA BASE. Son dos números muy distintos y
el que le importa a una persona es el primero: un dependiente baja la base en 72 UVT, pero lo que
baja el impuesto depende de la tarifa marginal de ESE contribuyente y puede ser cero.

Y CUANDO ES CERO, SE DICE. Con el impuesto ya en cero ningún beneficio baja un peso, así que
prometer "te ahorrarías $300.000 con prepagada" sería falso. `ninguno_ahorra` deja eso explícito una
sola vez, arriba, en vez de repetir la misma explicación en cada fila.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, computed_field

from declaras.caso import CasoTributario
from declaras.dinero import en_pesos
from declaras.motor import Flag
from declaras.parametros import ParametrosAnio, cargar
from declaras.services.conciliacion.mapeo import avisos
from declaras.services.conciliacion.modelos import Partida
from declaras.services.conciliacion.peticiones import (
    BENEFICIOS,
    ahorro_de,
    etiqueta_de_pregunta,
)
from declaras.services.conciliacion.respuestas import Respuesta


class EstadoBeneficio(StrEnum):
    """En qué situación está un beneficio respecto de esta declaración."""

    # El certificado llegó y la cifra ya está en el cálculo.
    APLICADO = "APLICADO"
    # Nadie ha dicho si lo tiene. Es la recomendación de verdad.
    DISPONIBLE = "DISPONIBLE"
    # Se contestó que no lo tiene. Se muestra igual, porque un "no" por error cuesta plata.
    DESCARTADO = "DESCARTADO"


class Recomendacion(BaseModel):
    """Un beneficio con lo que le ahorraría a esta persona, y por qué esa cifra es esa."""

    pregunta: str
    # El nombre legible del beneficio ("medicina prepagada"). Sin esto la pantalla mostraba
    # "AFC_FVP" y "DONACION_ESAL", que son claves de la tabla, no nombres.
    etiqueta: str
    estado: EstadoBeneficio
    razon: str
    pregunta_previa: str
    # Impuesto que se deja de pagar, en pesos. Cero significa "no baja nada", y `ahorro_por_que`
    # dice si eso es porque el beneficio no sirve o porque no se pudo calcular.
    ahorro: int
    # `True` cuando el número es el techo legal del beneficio y no una medición: cuánto pagó de
    # prepagada lo sabe el cliente, no nosotros. Quien pinta escribe "hasta $X".
    ahorro_es_techo: bool
    ahorro_por_que: str | None
    # ¿El cero es una medición o un "no sabemos"? Sin esto la sección concluía "ningún beneficio te
    # ahorra nada" cuando la verdad era que no se pudo calcular ninguno. Son conclusiones opuestas:
    # la primera dice "no salgas a buscar papeles", la segunda "falta resolver algo antes".
    medido: bool
    # El tope legal en pesos del año, para poder decir "hasta {tope} al año" sin hablar en UVT.
    tope: int | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def vale_la_pena(self) -> bool:
        """Si mover esto cambia lo que la persona paga.

        Un beneficio disponible con ahorro cero no es una recomendación: es información. Sin esta
        distinción la pantalla ordena por plata y arriba quedan ocho filas que no mueven nada.
        """
        return self.estado is not EstadoBeneficio.APLICADO and self.ahorro > 0


class Recomendaciones(BaseModel):
    """El catálogo completo, con la conclusión de arriba ya sacada."""

    items: list[Recomendacion]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ahorro_disponible(self) -> int:
        """Lo que sumarían los beneficios que todavía se pueden mover.

        Es una suma de techos, así que es un techo: se lee "hasta". No se suman los aplicados,
        porque esos ya están dentro de la cifra que la persona ve como su impuesto.
        """
        return sum(r.ahorro for r in self.items if r.vale_la_pena)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ninguno_ahorra(self) -> bool:
        """Ningún beneficio del catálogo baja un peso, y eso está MEDIDO.

        Pasa cuando el impuesto ya es cero, y es la información más útil de toda la sección: no
        vale la pena que la persona salga a buscar certificados.

        Exige que haya al menos una medición. Sin ese requisito, un caso donde ningún ahorro se pudo
        calcular daba `True` y la pantalla afirmaba "no te ahorras nada" sobre cero evidencia.
        """
        medidos = [r for r in self.items if r.medido]
        return bool(medidos) and not any(r.vale_la_pena for r in medidos)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sin_medir(self) -> int:
        """Cuántos beneficios no se pudieron medir.

        Si son todos, la sección no puede concluir nada y tiene que decirlo: lo que falta es
        resolver lo que bloquea el cálculo, no buscar certificados.
        """
        return sum(1 for r in self.items if not r.medido)


def derivar_recomendaciones(
    partidas: Sequence[Partida],
    respuestas: Sequence[Respuesta],
    caso: CasoTributario,
    *,
    p: ParametrosAnio | None = None,
) -> Recomendaciones:
    """El catálogo completo de beneficios medido contra el caso de hoy."""
    parametros = p if p is not None else cargar(caso.anio_gravable)
    apagadas = {r.pregunta for r in respuestas if not r.tiene}

    # Los avisos del cruce viajan a cada estimación por la misma razón que en las peticiones: con un
    # bloqueante vivo el optimizador se niega, y el ahorro sale como no estimable en vez de prometer
    # una cifra calculada sobre una base a la que le falta un ingreso.
    try:
        del_cruce: Sequence[Flag] = avisos(list(partidas))
    except (ValueError, NotImplementedError):
        del_cruce = []

    items: list[Recomendacion] = []
    for beneficio in BENEFICIOS:
        if beneficio.presente(caso):
            estado = EstadoBeneficio.APLICADO
        elif beneficio.pregunta in apagadas:
            estado = EstadoBeneficio.DESCARTADO
        else:
            estado = EstadoBeneficio.DISPONIBLE

        hipotesis = beneficio.hipotesis(caso, parametros) if beneficio.hipotesis else None
        medida = ahorro_de(caso, hipotesis, parametros, del_cruce)
        tope = beneficio.tope(parametros) if beneficio.tope is not None else None
        items.append(
            Recomendacion(
                pregunta=beneficio.pregunta,
                etiqueta=etiqueta_de_pregunta(beneficio.pregunta),
                estado=estado,
                razon=beneficio.razon.format(tope=en_pesos(tope) if tope else ""),
                pregunta_previa=beneficio.pregunta_previa,
                ahorro=medida.pesos,
                ahorro_es_techo=hipotesis is not None,
                ahorro_por_que=medida.por_que,
                medido=medida.medido,
                tope=tope,
            )
        )

    # Por plata descendente: lo que más mueve, arriba. El nombre desempata para que la lista no
    # baile entre consultas cuando varios valen lo mismo (típicamente cero).
    items.sort(key=lambda r: (-r.ahorro, r.pregunta))
    return Recomendaciones(items=items)
