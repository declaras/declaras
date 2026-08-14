"""Los bienes que nadie le reporta a la DIAN: la casa, el carro, la moto.

═══ POR QUE ESTO NO PASA POR EL CONCILIADOR ═══

Una partida tiene dos lados porque el hecho lo cuentan dos: el tercero que le reportó a la DIAN y
el documento que trae el cliente. Un inmueble no tiene dos lados. Ninguna notaría le reporta a la
DIAN, año tras año, que alguien SIGUE siendo dueño de su apartamento: reporta la compraventa el año
en que ocurrió y nunca más. Así que el patrimonio no se concilia, se CAPTURA, igual que la medicina
prepagada, y por eso vive al lado de `beneficios.py` y no dentro del cruce.

La diferencia con los beneficios es qué pasa si nadie pregunta. Un beneficio que no se pregunta
cuesta plata que se deja de ahorrar. Un bien que no se pregunta produce una declaración incompleta,
que es un problema distinto y peor: el patrimonio bruto es uno de los cinco topes de obligación
(4.500 UVT), así que un patrimonio corto puede decirle a alguien que no está obligado a declarar
cuando sí lo está. Por eso estas preguntas NO se ordenan por ahorro ni se cortan por umbral como las
peticiones: se contestan todas o el expediente no avanza.

═══ LAS DOS REGLAS DE VALORACION, QUE NO SON LA MISMA ═══

**Inmuebles, art. 277.** Quien no lleva contabilidad declara el inmueble por el MAYOR entre el costo
de adquisición, el costo fiscal, el autoavalúo y el avalúo catastral actualizado al cierre. Es un
máximo entre varias cifras, así que un solo papel nunca alcanza: el predial trae el avalúo, la
escritura trae lo que se pagó, y cuál de los dos gana depende del caso. Declarar solo el predial
subdeclara al que compró caro, y declarar solo la escritura subdeclara al que compró hace veinte
años.

**Vehículos, art. 267.** La regla general es el costo fiscal, o sea lo que se pagó. El recibo del
impuesto vehicular NO sirve para esto, aunque sea el papel que todo el mundo tiene a mano: ese
avalúo lo fija cada año el Ministerio de Transporte para cobrar un impuesto departamental (Ley 488
de 1998, art. 143) y suele ir muy por debajo del costo. Sirve para identificar el vehículo y como
control de plausibilidad, no como valor. El texto anterior del mismo art. 267 lo decía con todas las
letras: "incluidos los semovientes y vehículos automotores de uso personal... está constituido por
su precio de costo".

Y no hay depreciación que valga: una persona natural que no lleva contabilidad no deprecia su carro,
así que el valor NO baja con los años. Es lo primero que sorprende a quien lo ve.

El reajuste opcional del art. 70 se deja apagado a propósito. Sube el patrimonio declarado sin dar
nada a cambio en el año, y solo paga cuando el bien se venda. Es una elección del contribuyente, no
un default que el sistema deba tomar por él.

═══ LA DEUDA VIAJA CON EL BIEN ═══

Capturar la casa sin la hipoteca no es "medio dato": es un dato que hace daño. El patrimonio líquido
pega un salto que las rentas del año no justifican, y `motor/cierre.py` levanta la alerta de
comparación patrimonial (art. 236) en todos los casos con inmueble financiado. Por eso el saldo de
la deuda es un campo del bien y no una lista aparte que alguien pueda olvidar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from declaras.caso import Activo, Deuda, Fuente, Patrimonio
from declaras.motor import Flag
from declaras.services.conciliacion.modelos import _Modelo
from declaras.services.conciliacion.respuestas import Respuesta

# Las claves de las compuertas. Son `Respuesta` como cualquier otra pregunta del expediente, y por
# la misma razón: un "no tengo carro" tiene que persistir, o el sistema lo vuelve a preguntar en
# cada consulta y el cliente cree que nadie lo escucha.
INMUEBLES = "INMUEBLES"
VEHICULOS = "VEHICULOS"
OTROS_BIENES = "OTROS_BIENES"

TipoBien = Literal["inmueble", "vehiculo", "otro"]


class BienCapturado(_Modelo):
    """Un bien que alguien declaró tener, con los insumos de su valoración.

    Los insumos se guardan CRUDOS y separados (lo que costó, lo que dice el predial) en vez de un
    solo `valor` ya resuelto. Dos razones. Una, que la regla del art. 277 es un máximo entre ellos y
    con un valor resuelto no se puede volver a explicar cuál ganó ni rehacer la cuenta si aparece el
    otro papel. Dos, que el contador tiene que poder defender la cifra frente a la DIAN, y "es el
    avalúo del predial porque era mayor que los $80.000.000 de la escritura" se defiende; "es
    $120.000.000" no.
    """

    id: str
    tipo: TipoBien
    # Como lo reconoce su dueño: "Apartamento 502, Calle 100" o "Mazda 3 gris". No es un campo
    # decorativo: cuando hay tres inmuebles, es lo único que distingue una tarjeta de otra.
    descripcion: str
    # Matrícula inmobiliaria o placa. Opcional porque nadie se la sabe de memoria y exigirla
    # frenaría la captura, que es justo lo que hay que evitar.
    identificacion: str | None = None
    costo_adquisicion: int | None = Field(default=None, ge=0)
    # Lo que dice el recibo o la declaración del predial. En Bogotá y Medellín es un autoavalúo y
    # en el resto es el avalúo catastral; para el art. 277 los dos entran al mismo máximo, así que
    # se guardan en un solo campo en vez de pedirle al cliente que sepa en cuál de los dos vive.
    avaluo_catastral: int | None = Field(default=None, ge=0)
    # La salida de emergencia: el bien existe, el papel no aparece, y el contador responde por la
    # cifra. Gana sobre todo lo demás porque es una decisión tomada, pero deja aviso.
    valor_declarado: int | None = Field(default=None, ge=0)
    deuda_saldo: int | None = Field(default=None, ge=0)
    deuda_acreedor: str | None = None
    # Solo motos: decide si tiene sentido pedir el recibo del impuesto vehicular. Las de hasta
    # 125 c.c. están excluidas del impuesto (Ley 488 de 1998, art. 141), así que ese recibo no
    # existe y pedirlo es quemar la confianza del cliente en la primera pregunta.
    cilindraje: int | None = Field(default=None, ge=0)
    quien: str
    cuando: datetime


class Valoracion(_Modelo):
    """El valor que va al 210, por qué es ese, y bajo qué norma.

    LA NORMA VA EN SU PROPIO CAMPO y no dentro de la frase, porque esta cifra la leen dos personas
    distintas. Al contador "art. 277" le dice todo: es lo que va a citar si la DIAN pregunta. Al
    titular no le dice nada y le pone encima un número de artículo en medio de una frase que iba
    entendiendo. Con un solo texto había que elegir a quién dejar afuera; con dos campos la
    pantalla arma la frase que le sirve a cada uno.
    """

    valor: int
    regla: str
    norma: str | None = None
    # Qué falta para poder sostener la cifra, si falta algo. `None` es "está completo".
    falta: str | None = None


_ART_INMUEBLES = "art. 277 ET"
_ART_GENERAL = "art. 267 ET"


def valorar(bien: BienCapturado) -> Valoracion:
    """El valor patrimonial de un bien, con la regla que lo produjo."""
    if bien.valor_declarado is not None:
        return Valoracion(
            valor=bien.valor_declarado,
            regla="el valor que declaró el contribuyente, sin un papel que lo respalde",
            falta="Queda declarado sin soporte. Si la DIAN lo pregunta, hay que poder mostrarlo.",
        )

    if bien.tipo == "inmueble":
        return _valorar_inmueble(bien)

    # Vehículos y todo lo demás: costo fiscal. El avalúo del Ministerio de Transporte no entra ni
    # como candidato, justamente para que nadie lo meta por costumbre.
    if bien.costo_adquisicion is None:
        return Valoracion(
            valor=0,
            regla="todavía sin valor",
            norma=_ART_GENERAL,
            falta=(
                "Falta el precio de compra. No sirve el avalúo del impuesto vehicular, que es "
                "más bajo y existe para cobrar otro impuesto."
            ),
        )
    return Valoracion(valor=bien.costo_adquisicion, regla="lo que costó", norma=_ART_GENERAL)


def _valorar_inmueble(bien: BienCapturado) -> Valoracion:
    """El mayor entre lo que costó y el avalúo, que es lo que pide el art. 277."""
    avaluo, costo = bien.avaluo_catastral, bien.costo_adquisicion

    if avaluo is None and costo is None:
        return Valoracion(
            valor=0,
            regla="todavía sin valor",
            norma=_ART_INMUEBLES,
            falta=(
                "Falta el avalúo del predial o el precio de compra. Se declara el mayor de los "
                "dos, así que sin ninguno no hay cifra."
            ),
        )

    if avaluo is not None and costo is not None:
        if avaluo >= costo:
            return Valoracion(
                valor=avaluo,
                regla="el avalúo del predial, que es mayor que el precio de compra",
                norma=_ART_INMUEBLES,
            )
        return Valoracion(
            valor=costo,
            regla="lo que costó, que es mayor que el avalúo del predial",
            norma=_ART_INMUEBLES,
        )

    if avaluo is not None:
        return Valoracion(
            valor=avaluo,
            regla="el avalúo del predial",
            norma=_ART_INMUEBLES,
            falta=(
                "Falta el precio de compra. Si fue por más que el avalúo, la cifra sube, "
                "porque se declara el mayor de los dos."
            ),
        )
    return Valoracion(
        valor=costo or 0,
        regla="lo que costó",
        norma=_ART_INMUEBLES,
        falta=(
            "Falta el avalúo del predial. Si es mayor que el precio de compra, la cifra sube, "
            "porque se declara el mayor de los dos."
        ),
    )


def a_patrimonio(
    bienes: list[BienCapturado], *, patrimonio_liquido_anterior: int | None = None
) -> tuple[Patrimonio, list[Flag]]:
    """Los bienes capturados, convertidos en lo que el motor liquida.

    Devuelve avisos junto con el patrimonio, no en vez de él: un bien mal soportado ENTRA a la
    declaración —dejarlo fuera subdeclararía, que es lo que la sanción castiga— y queda señalado
    para que el contador decida antes de presentar.
    """
    activos: list[Activo] = []
    deudas: list[Deuda] = []
    avisos: list[Flag] = []

    for bien in bienes:
        valoracion = valorar(bien)
        fuente = Fuente(
            clase="manual",
            ref=bien.quien,
            detalle=(
                f"{valoracion.regla} ({valoracion.norma})" if valoracion.norma else valoracion.regla
            ),
        )
        activos.append(
            Activo(
                # `otro` del capturado y `otro` de la exógena significan lo mismo (un activo que
                # suma a R29 sin más detalle), así que el Literal del caso no necesita ampliarse.
                tipo=bien.tipo,
                descripcion=bien.descripcion,
                valor_31dic=valoracion.valor,
                fuente=fuente,
            )
        )
        if valoracion.falta:
            avisos.append(
                Flag(
                    codigo="BIEN_SIN_SOPORTE",
                    mensaje=f"{bien.descripcion}: {valoracion.falta}",
                )
            )
        if bien.deuda_saldo:
            deudas.append(
                Deuda(
                    acreedor=bien.deuda_acreedor or f"deuda de {bien.descripcion}",
                    saldo_31dic=bien.deuda_saldo,
                    fuente=fuente,
                )
            )

    return (
        Patrimonio(
            activos=activos,
            deudas=deudas,
            patrimonio_liquido_anterior=patrimonio_liquido_anterior,
        ),
        avisos,
    )


class PreguntaPatrimonio(_Modelo):
    """Una compuerta del cuestionario: qué se pregunta y qué papel resuelve el sí."""

    pregunta: str
    tipo: TipoBien
    texto: str
    # La misma pregunta para quien NO es el contribuyente. El contador no tiene casa a su nombre:
    # revisa la de otro, y tutearlo es la voz del titular filtrandose a la pantalla equivocada.
    texto_contador: str
    # Para qué sirve contestarla. No es el ahorro (estas preguntas no ahorran nada), es la razón
    # por la que hay que contestarla igual.
    por_que: str
    documento: str
    copy_sugerido: str


PREGUNTAS: tuple[PreguntaPatrimonio, ...] = (
    PreguntaPatrimonio(
        pregunta=INMUEBLES,
        tipo="inmueble",
        texto="¿Tienes casa, apartamento, lote, local, finca o parqueadero a tu nombre?",
        texto_contador=(
            "¿El cliente tiene inmuebles? Casa, apartamento, lote, local, finca o parqueadero."
        ),
        por_que=(
            "Un inmueble no lo reporta nadie año tras año, y el patrimonio bruto define uno de "
            "los topes que obligan a declarar."
        ),
        documento=(
            "El recibo o la declaración del impuesto predial del año, y el valor de compra "
            "(está en la escritura)."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito saber si tienes algún inmueble a tu "
            "nombre: casa, apartamento, lote, local, finca o parqueadero. Si sí, mándame el "
            "recibo del predial de este año y dime en cuánto lo compraste. La DIAN no ve esto "
            "sola y tiene que ir en la declaración."
        ),
    ),
    PreguntaPatrimonio(
        pregunta=VEHICULOS,
        tipo="vehiculo",
        texto="¿Tienes carro o moto a tu nombre?",
        texto_contador="¿El cliente tiene carro o moto a su nombre?",
        por_que=(
            "Un vehículo se declara por lo que costó, y ese dato no llega por ningún reporte."
        ),
        documento=(
            "La factura o el contrato de compraventa. El recibo del impuesto vehicular sirve "
            "para identificarlo, pero no es el valor que pide la ley."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta: ¿tienes carro o moto a tu nombre? Si sí, dime "
            "la placa y en cuánto lo compraste. Ojo, es el valor de compra, no el avalúo del "
            "impuesto vehicular, que es otra cosa y es más bajo."
        ),
    ),
    PreguntaPatrimonio(
        pregunta=OTROS_BIENES,
        tipo="otro",
        texto=(
            "¿Tienes otros bienes? Cuentan cuentas en el exterior, criptomonedas, acciones, "
            "participaciones en empresas y préstamos que te deban."
        ),
        texto_contador=(
            "¿El cliente tiene otros bienes? Cuentas en el exterior, criptomonedas, acciones, "
            "participaciones en sociedades y cuentas por cobrar."
        ),
        por_que=(
            "Las cuentas y los saldos de bancos colombianos ya llegan por la exógena. Lo de "
            "afuera y lo que no pasa por un banco, no."
        ),
        documento="El extracto o el certificado de la entidad al 31 de diciembre.",
        copy_sugerido=(
            "Hola. Para cerrar tu declaración: ¿tienes cuentas en el exterior, criptomonedas, "
            "acciones o participaciones en alguna empresa, o plata que te deban? Lo de los "
            "bancos colombianos ya lo tengo, es lo demás lo que necesito."
        ),
    ),
)


def sin_contestar(
    respuestas: list[Respuesta], bienes: list[BienCapturado]
) -> list[PreguntaPatrimonio]:
    """Las compuertas que nadie ha contestado todavía.

    UN BIEN CARGADO CONTESTA SU PROPIA PREGUNTA. Si el expediente tiene un apartamento, decir
    "falta contestar si tiene inmuebles" es absurdo para quien lo lee: el hecho es una respuesta
    más fuerte que la respuesta. Sin esto, capturar un bien por fuera del cuestionario (el agente
    por WhatsApp, una carga del contador) dejaba el expediente bloqueado por una pregunta que la
    realidad ya había contestado.

    Un `tiene=True` sin bienes cargados NO cuenta como pendiente acá: la pregunta ya se contestó y
    lo que falta es el detalle, que es otra cosa y se señala aparte. Mezclarlas dejaría la compuerta
    parpadeando entre contestada y sin contestar cada vez que alguien borra un bien.
    """
    contestadas = {r.pregunta for r in respuestas}
    con_bienes = {b.tipo for b in bienes}
    return [p for p in PREGUNTAS if p.pregunta not in contestadas and p.tipo not in con_bienes]


def dijo_que_si_y_no_cargo(
    respuestas: list[Respuesta], bienes: list[BienCapturado]
) -> list[PreguntaPatrimonio]:
    """Las compuertas contestadas que sí y todavía no tienen un solo bien.

    Es el estado a medio camino, y es el que más se le olvida a la gente: contestó "sí tengo carro"
    y ahí quedó. Sin esto el expediente parecería completo con el vehículo por fuera.
    """
    dijo_si = {r.pregunta for r in respuestas if r.tiene}
    con_bienes = {b.tipo for b in bienes}
    return [p for p in PREGUNTAS if p.pregunta in dijo_si and p.tipo not in con_bienes]


def falta_por_contestar(respuestas: list[Respuesta], bienes: list[BienCapturado]) -> list[str]:
    """Por qué el patrimonio no está completo, en frases que se le pueden mostrar a alguien."""
    pendientes = [f"Falta contestar: {p.texto}" for p in sin_contestar(respuestas, bienes)]
    pendientes += [
        f"Contestó que sí pero no hay ninguno cargado: {p.texto}"
        for p in dijo_que_si_y_no_cargo(respuestas, bienes)
    ]
    return pendientes
