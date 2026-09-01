"""Las peticiones: qué documento falta, por qué, y en qué orden pedirlo.

SE DERIVAN, NO SE ALMACENAN. La lista sale de tres cosas que ya existen —las partidas del
cruce, las `Respuesta` del cliente y el caso que hay hoy— así que nunca queda desfasada:
cuando el 220 de ACME llega, su partida deja de ser `SOLO_DIAN` y la petición desaparece
sola. Lo único persistido es la `Respuesta`, y un `tiene=False` apaga la petición PARA
SIEMPRE: sin ese registro el sistema le pregunta por prepagada al cliente en cada consulta.

TRES ORÍGENES, UN SOLO ESPACIO DE CLAVES. `Peticion.id` es la clave estable con que se
apaga: para un beneficio invisible es su pregunta (`PREPAGADA`), para un documento de
tercero es `partida:{id}`. Así `POST /respuestas` (el cliente dice "no tengo") y
`POST /cerrar-peticion` (el contador cierra sin soporte) escriben LO MISMO —una `Respuesta`
con `tiene=False` sobre esa clave— y no hay dos mecanismos que puedan divergir.

EL AHORRO ES ORIENTACIÓN, NO PROMESA. Sale de `optimizador.ahorro_marginal` sobre una
hipótesis explícita (el tope legal del beneficio, las tarifas de ley de los aportes), y
los ahorros marginales NO son aditivos: sumarlos sobreestima hasta 64% cuando el tope del
40% se copa. Lo que se le dice al cliente es el delta REAL tras incorporar el documento
(`GET /liquidacion`), no esta cifra. Una hipótesis que no se puede construir sin inventar
plata (cuánto depositó en el AFC, qué parte del dividendo es no gravada) reporta 0 = "no
estimable", y el orden la deja al final en vez de fabricarle un número que nadie sostiene.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from declaras.caso import (
    CasoTributario,
    Dependiente,
    Fuente,
    IngresoLaboral,
    MontoDeclarado,
)
from declaras.dinero import en_pesos, porcentaje
from declaras.motor import Flag
from declaras.optimizador import ahorro_marginal, optimizar
from declaras.parametros import ParametrosAnio, cargar
from declaras.services.conciliacion.conceptos import CONCEPTOS_FUERA_DEL_MOTOR, Concepto
from declaras.services.conciliacion.mapeo import avisos
from declaras.services.conciliacion.modelos import EstadoPartida, Partida, _Modelo
from declaras.services.conciliacion.respuestas import Respuesta

# Los dos cortes de la lista. Una cola de treinta peticiones no se trabaja: el contador
# atiende las primeras y el resto se vuelve ruido, así que la lista se corta por plata
# (una petición que ahorra menos que esto cuesta más incomodar al cliente que lo que trae)
# y por cantidad. El 0 NO se corta: es "no estimable", no "no sirve" — el certificado que
# trae la retención de un banco no baja el impuesto y aun así hay que pedirlo.
UMBRAL_AHORRO = 50_000
MAXIMO_PETICIONES = 10

# Las tarifas de ley del aporte obligatorio del EMPLEADO (art. 204 y 20 de la Ley 100):
# el único ingrediente del 220 que se puede estimar sin ver el documento, porque no
# depende de lo que el empleador haya hecho sino de la ley. El fondo de solidaridad de
# los ingresos altos se ignora a propósito: sumarlo subiría la estimación sin soporte.
_APORTE_SALUD_PCT = 0.04
_APORTE_PENSION_PCT = 0.04

# La proveniencia de un hecho que NO se declara: vive solo dentro de la hipótesis que se
# le pasa al motor para medir el ahorro, y esa liquidación nunca se persiste ni se imprime.
_FUENTE_HIPOTESIS = Fuente.manual("hipótesis de ahorro")


class _Ahorro(NamedTuple):
    # `medido` distingue las dos razones por las que un ahorro puede ser cero, y hace falta una
    # BANDERA y no el texto: la razón está escrita en español para que la lea una persona, y quien
    # tenga que ramificar sobre ella no puede hacerlo buscando subcadenas. Un cero medido significa
    # "el beneficio no baja el impuesto" (no vale la pena pedir el documento); un cero sin medir
    # significa "no sabemos" (no se puede concluir nada). Colapsarlas era el bug: la pantalla decía
    # "ningún beneficio te ahorra nada" cuando la verdad era que no se pudo calcular ninguno.
    """Lo que devuelve `_ahorro`, con nombres para que no se confunda el orden."""

    pesos: int
    por_que: str | None
    medido: bool


class Peticion(_Modelo):
    """Un documento (o una pregunta) que le falta al expediente, con su porqué.

    `tercero` es el dueño del documento cuando lo hay (el empleador, el banco): es lo que
    el contador necesita para saber a quién pedírselo. Los beneficios invisibles no tienen
    tercero conocido — nadie le reportó a la DIAN la prepagada del cliente.

    `pregunta_previa` distingue las dos fases de un beneficio invisible: mientras nadie
    haya contestado, la petición es una PREGUNTA (pedir un certificado que quizá no exista
    quema la confianza del cliente); con el `sí` ya dado, es una petición de documento.
    """

    id: str
    tipo_documento: str
    tercero: dict[str, str] | None
    razon: str
    ahorro_estimado: int
    # ¿El ahorro es el TECHO legal del beneficio o una cifra medida? Campo adicional al
    # contrato del plan, por la misma razón por la que las marcas de T4/T5 son
    # estructurales y no texto: los dos números se ordenan en la misma lista y significan
    # cosas distintas. El del 220 se mide sobre tarifas de ley aplicadas al pago que la
    # exógena ya reportó; el de la prepagada es "hasta 16 UVT al mes" y depende de lo que
    # el cliente haya pagado. Con un solo `int` la interfaz diría "$5.900.000" de los dos y
    # el contador le prometería al cliente una cifra que nadie sostiene. La UI escribe
    # "hasta $X" cuando esto es True.
    ahorro_es_techo: bool
    # POR QUE el ahorro es el que es, cuando no es una simple medicion. Sin esto, tres
    # situaciones que llevan a decisiones distintas se ven iguales en pantalla: un beneficio que
    # de verdad no baja nada (no vale la pena molestar al cliente), uno que no se puede calcular
    # todavia (hay que desbloquear el caso primero) y uno que baja cero pesos porque el impuesto
    # ya es cero. Las tres se mostraban como "$ 0".
    ahorro_por_que: str | None = None
    prioridad: int
    pregunta_previa: str | None
    copy_sugerido: str


# ─────────────────────────── el catálogo de beneficios invisibles ───────────────────────────
#
# "Invisible" es literal: no aparece en la exógena ni en ningún documento que el portal
# entregue, así que si nadie pregunta, esa plata se pierde. El catálogo es la razón de ser
# del producto y por eso los textos viven acá, en una tabla, y no improvisados por llamada.
#
# `BENEFICIOS`, `ahorro_de` y `en_pesos` NO llevan underscore porque no son privados: los usa
# también `recomendaciones.py`, que recorre el catálogo completo en vez de la cola de pendientes.
# Dos implementaciones del mismo ahorro terminarían diciendo cifras distintas para el mismo
# beneficio, y ganaría la que se vea primero. Idealmente el catálogo viviría en su propio módulo del
# que importaran los dos; no se movió porque son cuatrocientas líneas de tabla y el cambio de nombre
# ya deja claro qué es compartido.


@dataclass(frozen=True)
class _Beneficio:
    """Un beneficio que hay que preguntar, con su copy y su hipótesis de ahorro."""

    pregunta: str
    tipo_documento: str
    razon: str
    pregunta_previa: str
    copy_sugerido: str
    # ¿Ya está capturado en el caso? Si sí, el certificado llegó y no hay nada que pedir.
    #
    # RECIBE EL CASO COMPLETO, no solo `beneficios`, y eso amplió lo que el catálogo puede expresar:
    # el certificado del promedio salarial de las cesantías no vive en `Beneficios` sino en cada
    # `IngresoLaboral`, así que con la firma anterior ese beneficio no cabía en la tabla y habría
    # tenido que manejarse por fuera, con su propia forma de calcular el ahorro.
    presente: Callable[[CasoTributario], bool]
    # El caso con el beneficio en su TECHO legal, o None cuando el techo no se puede
    # afirmar sin inventar plata del cliente (ahí el ahorro se reporta como no estimable).
    # Todo lo que salga de acá es un techo, nunca una medición: lo marca `ahorro_es_techo`.
    hipotesis: Callable[[CasoTributario, ParametrosAnio], CasoTributario] | None
    # El tope legal EN PESOS del año. Los textos lo interpolan como `{tope}` en vez de escribir
    # "hasta 1.200 UVT": el mensaje de `copy_sugerido` se le manda al cliente por WhatsApp, y una
    # UVT no significa nada para quien lo va a leer. Además cambia cada año, así que escribirlo a
    # mano en pesos envejecería mal.
    tope: Callable[[ParametrosAnio], int] | None = None


def _con_beneficios(caso: CasoTributario, **cambios: object) -> CasoTributario:
    """El caso con un beneficio añadido. Las claves son literales del modelo y hay tests
    que fijan el ahorro resultante: `model_copy(update=...)` no respeta `extra="forbid"`
    y un typo sería un no-op silencioso (un ahorro estimado en 0 sin que nadie lo note)."""
    beneficios = caso.beneficios.model_copy(update=cambios)
    return caso.model_copy(update={"beneficios": beneficios})


def _monto(valor: int) -> MontoDeclarado:
    return MontoDeclarado(valor=valor, fuente=_FUENTE_HIPOTESIS)


# Como se nombra cada pregunta cuando hay que contarla en una frase, no ofrecerla. Se usa en la
# bitacora: "No tiene medicina prepagada" se lee; "No tiene PREPAGADA" no.
ETIQUETAS_DE_PREGUNTA = {
    "PREPAGADA": "medicina prepagada",
    "INTERESES_VIVIENDA": "crédito de vivienda",
    "DEPENDIENTES": "personas a cargo",
    "AFC_FVP": "aportes a AFC o pensiones voluntarias",
    "ICETEX": "crédito educativo del ICETEX",
    "GMF": "gravamen a los movimientos financieros",
    "DONACION_ESAL": "donaciones a entidades sin ánimo de lucro",
    "PROMEDIO_CESANTIAS": "la certificación del promedio salarial de tus cesantías",
}


def etiqueta_de_pregunta(pregunta: str) -> str:
    """Nombre legible de una pregunta, para contarla en una frase.

    Las derivadas del cruce llegan como `partida:{id}`, y ese id es `nit:CONCEPTO`: volcarlo
    dejaba "No tiene partida:901303824:salarios" en la pantalla. De ahí solo se puede sacar el
    concepto, que es lo único que significa algo para quien lo lee.
    """
    directa = ETIQUETAS_DE_PREGUNTA.get(pregunta)
    if directa:
        return directa
    if pregunta.startswith("partida:"):
        concepto = pregunta.rsplit(":", 1)[-1].replace("_", " ").lower()
        return f"el soporte de {concepto}"
    return pregunta.replace("_", " ").lower()


BENEFICIOS: tuple[_Beneficio, ...] = (
    _Beneficio(
        pregunta="PREPAGADA",
        tipo_documento="CERT_PREPAGADA",
        razon=(
            "La medicina prepagada es deducible hasta {tope} al año y la DIAN no la ve: "
            "sin el certificado de la aseguradora esa plata no entra al 210."
        ),
        pregunta_previa="¿Pagaste medicina prepagada o un plan complementario de salud?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: ¿tuviste medicina prepagada o plan "
            "complementario de salud? Si sí, mándame el certificado anual que emite la "
            "aseguradora (Colsanitas, Sura, Coomeva, Medplus...). Es una deducción que la "
            "DIAN no ve sola y puede bajarte bastante el impuesto."
        ),
        presente=lambda c: c.beneficios.medicina_prepagada is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, medicina_prepagada=_monto(p.uvt_pesos(p.prepagada_tope_uvt_anio))
        ),
        tope=lambda p: p.uvt_pesos(p.prepagada_tope_uvt_anio),
    ),
    _Beneficio(
        pregunta="DEPENDIENTES",
        tipo_documento="SOPORTE_DEPENDIENTE",
        razon=(
            "Cada dependiente descuenta {tope} por fuera del límite del 40%, y además destraba "
            "la deducción del artículo 387, y ningún tercero "
            "le reporta a la DIAN que el cliente tiene hijos o padres a cargo."
        ),
        pregunta_previa=(
            "¿Tienes personas a cargo? Cuentan hijos menores de 18, hijos estudiando "
            "hasta los 23, hijos o cónyuge con discapacidad, y padres o hermanos que "
            "dependan de ti económicamente."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta: ¿tienes personas a cargo? Cuentan hijos "
            "menores de 18, hijos estudiando hasta los 23, cónyuge o hijos con "
            "discapacidad, y padres o hermanos que dependan de ti. Mándame el registro "
            "civil (o el certificado de estudio / la certificación médica según el caso). "
            "Es uno de los beneficios más grandes y la DIAN nunca lo sabe sola."
        ),
        presente=lambda c: bool(c.beneficios.dependientes),
        hipotesis=lambda caso, p: _con_beneficios(
            caso, dependientes=[Dependiente(tipo="hijo_menor", fuente=_FUENTE_HIPOTESIS)]
        ),
        tope=lambda p: p.uvt_pesos(72),
    ),
    _Beneficio(
        pregunta="INTERESES_VIVIENDA",
        tipo_documento="CERT_INTERESES_VIVIENDA",
        razon=(
            "Los intereses del crédito de vivienda son deducibles hasta {tope}; el "
            "banco los certifica una vez al año y la exógena no los trae desagregados."
        ),
        pregunta_previa="¿Tienes crédito de vivienda o leasing habitacional?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si tienes crédito de vivienda o leasing "
            "habitacional, mándame el certificado de intereses del año que emite el banco "
            "(lo descargas desde la banca en línea). Los intereses son deducibles hasta "
            "{tope}."
        ),
        presente=lambda c: c.beneficios.intereses_vivienda is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, intereses_vivienda=_monto(p.uvt_pesos(p.intereses_vivienda_tope_uvt))
        ),
        tope=lambda p: p.uvt_pesos(p.intereses_vivienda_tope_uvt),
    ),
    _Beneficio(
        pregunta="ICETEX",
        tipo_documento="CERT_ICETEX",
        razon=(
            "Los intereses de un crédito educativo del ICETEX son deducibles hasta "
            "{tope} y solo constan en el certificado de la entidad."
        ),
        pregunta_previa="¿Pagaste intereses de un crédito educativo del ICETEX?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si tienes crédito educativo con el "
            "ICETEX, mándame el certificado de intereses del año. Son deducibles hasta "
            "{tope}."
        ),
        presente=lambda c: c.beneficios.intereses_icetex is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, intereses_icetex=_monto(p.uvt_pesos(p.icetex_tope_uvt))
        ),
        tope=lambda p: p.uvt_pesos(p.icetex_tope_uvt),
    ),
    _Beneficio(
        pregunta="AFC_FVP",
        tipo_documento="CERT_AFC_FVP",
        razon=(
            "Los aportes voluntarios a AFC o a un fondo de pensiones voluntarias son "
            "renta exenta hasta el 30% del ingreso; el certificado lo emite la entidad."
        ),
        pregunta_previa=(
            "¿Hiciste aportes voluntarios a una cuenta AFC o a un fondo de pensiones voluntarias?"
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si hiciste aportes a una cuenta AFC o a "
            "un fondo de pensiones voluntarias, mándame el certificado anual de la "
            "entidad. Son renta exenta hasta el 30% de tu ingreso."
        ),
        presente=lambda c: bool(c.beneficios.aportes_afc_fvp),
        # Sin hipótesis a propósito: el techo legal (30% del ingreso, 3.800 UVT) no es una
        # estimación sino el máximo que la ley permite, y usarlo pondría esta petición
        # primera en la lista con una cifra que depende SOLO de cuánto depositó el cliente
        # —dato que nadie tiene todavía—. Se reporta como no estimable.
        hipotesis=None,
    ),
    _Beneficio(
        pregunta="DONACION_ESAL",
        tipo_documento="CERT_DONACION_ESAL",
        razon=(
            "Una donación a una entidad sin ánimo de lucro del régimen especial da "
            "descuento del 25%, y solo la prueba el certificado de la entidad."
        ),
        pregunta_previa="¿Donaste a una fundación, iglesia u organización sin ánimo de lucro?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si donaste a una fundación, iglesia u "
            "organización sin ánimo de lucro, mándame el certificado de la donación. "
            "Da un descuento del 25% del valor donado."
        ),
        presente=lambda c: bool(c.beneficios.donaciones_esal),
        # Sin hipótesis: cuánto donó es un dato del cliente, no un tope de ley.
        hipotesis=None,
    ),
    _Beneficio(
        pregunta="GMF",
        tipo_documento="CERT_GMF",
        razon=(
            "El 50% del gravamen a los movimientos financieros (el 4x1000) es deducible; "
            "el banco lo certifica y la exógena no lo trae."
        ),
        pregunta_previa="¿Quieres que pidamos el certificado del 4x1000 a tu banco?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: mándame el certificado del 4x1000 "
            "(gravamen a los movimientos financieros) del año, que lo descargas de la "
            "banca en línea. La mitad de lo que pagaste es deducible."
        ),
        presente=lambda c: c.beneficios.gmf_pagado is not None,
        # Sin hipótesis: depende de cuánto se movió en las cuentas.
        hipotesis=None,
    ),
    _Beneficio(
        pregunta="PROMEDIO_CESANTIAS",
        tipo_documento="CERT_PROMEDIO_CESANTIAS",
        razon=(
            "El auxilio de cesantías es exento si el ingreso mensual promedio de los últimos "
            "seis meses de vinculación no pasó de {tope} (art. 206 num. 4); por encima queda "
            "exento un porcentaje. Ese promedio suele venir en la exógena, pero este empleador "
            "no lo reportó, así que sin la certificación las cesantías entran gravadas completas."
        ),
        pregunta_previa=(
            "¿Te pagaron o te consignaron cesantías este año? Si sí, el empleador puede "
            "certificar tu salario promedio de los últimos seis meses."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si te pagaron o consignaron cesantías, "
            "pídele a tu empleador (o a Gestión Humana) una certificación con tu salario "
            "promedio de los últimos seis meses. Con ese dato las cesantías pueden quedar "
            "exentas hasta {tope} al mes de promedio, y eso baja el impuesto."
        ),
        # No es un beneficio de `Beneficios`: es un dato que le falta a un ingreso que ya está. Por
        # eso `presente` mira `laborales`, que es lo que el cambio de firma habilitó. Si nadie tiene
        # cesantías, no hay nada que pedir y cuenta como presente.
        presente=lambda c: all(
            x.promedio_mensual_6m is not None for x in c.laborales if x.cesantias_e_intereses
        ),
        # La hipótesis es el mejor caso legal: un promedio en el tope de la exención total, que es
        # lo máximo que este certificado puede llegar a ahorrar. Cuánto ganaba de verdad lo sabe el
        # cliente, así que la cifra se marca como techo igual que las demás.
        hipotesis=lambda caso, p: _con_promedio_de_cesantias(
            caso, p.uvt_pesos(p.cesantias_exentas_tope_uvt_mes)
        ),
        tope=lambda p: p.uvt_pesos(p.cesantias_exentas_tope_uvt_mes),
    ),
)


def _con_promedio_de_cesantias(caso: CasoTributario, promedio: int) -> CasoTributario:
    """El caso suponiendo ese promedio salarial en los vínculos a los que les falta.

    Los que ya tienen el dato no se tocan: su exención ya está medida y sobrescribirla inflaría el
    ahorro que se le atribuye al certificado que falta.
    """
    laborales = [
        x.model_copy(update={"promedio_mensual_6m": promedio})
        if x.cesantias_e_intereses and x.promedio_mensual_6m is None
        else x
        for x in caso.laborales
    ]
    return caso.model_copy(update={"laborales": laborales})


# ─────────────────────────── el catálogo de certificados de tercero ───────────────────────────
#
# Qué documento le falta a una partida que solo sostiene la DIAN, y qué aporta ese
# documento que la exógena no tenga. La tabla y el frozenset de abajo PARTEN `Concepto`
# completo: un concepto nuevo que nadie clasifique revienta en vez de dejar de pedir su
# documento en silencio (la lección de I1 de la ronda 2 de T5, aplicada acá).


@dataclass(frozen=True)
class _Certificado:
    tipo_documento: str
    razon: str
    copy_sugerido: str


_CERTIFICADO_POR_CONCEPTO: dict[Concepto, _Certificado] = {
    Concepto.SALARIOS: _Certificado(
        tipo_documento="CERT_INGRESOS_220",
        razon=(
            "La exógena trae el pago total pero no los aportes obligatorios a salud y "
            "pensión (que son INCRNGO) ni la retención practicada: las dos cosas las "
            "certifica el formulario 220 del empleador."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito el certificado de ingresos y "
            "retenciones (formulario 220) que te da {tercero}. Se pide a nómina o a "
            "gestión humana y suele salir en marzo. Con él quedan los aportes a salud y "
            "pensión y la retención que ya te descontaron."
        ),
    ),
    Concepto.PENSIONES: _Certificado(
        # NO es un 220: el lector del 220 RECHAZA un certificado que reporte pensiones
        # (`Motivo220.TIENE_PENSIONES`), justamente porque la exención pensional es mensual
        # y registrarla como laboral cambia el impuesto. Pedir un 220 acá mandaba a subir el
        # único documento que el sistema iba a devolver.
        tipo_documento="CERT_PENSION",
        razon=(
            "La exención pensional es POR MES, y la exógena solo trae el total del año: "
            "sin el certificado del pagador las mesadas se reparten parejas y un "
            "retroactivo queda mal repartido."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito el certificado de pensión de "
            "{tercero} con el detalle de las mesadas del año. La parte exenta de la pensión "
            "se calcula mes por mes, así que el total anual no basta."
        ),
    ),
    Concepto.RENDIMIENTOS: _Certificado(
        tipo_documento="CERT_BANCARIO",
        razon=(
            "El certificado del banco trae la retención practicada y el componente "
            "inflacionario, que la exógena no desagrega."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito el certificado de rendimientos "
            "financieros del año de {tercero} (lo descargas de la banca en línea). Trae "
            "la retención que ya te practicaron."
        ),
    ),
    Concepto.ARRENDAMIENTOS: _Certificado(
        tipo_documento="CERT_ARRIENDO",
        razon=(
            "El canon reportado no dice qué retención se practicó ni qué costos "
            "(predial, administración, comisión) son descontables."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito el certificado de retención por "
            "arrendamientos de {tercero}, y los soportes de predial, administración y "
            "comisión de la inmobiliaria: esos costos se descuentan del canon."
        ),
    ),
    Concepto.DIVIDENDOS: _Certificado(
        tipo_documento="CERT_DIVIDENDOS",
        razon=(
            "La exógena trae un solo número y el 210 exige separar la parte no gravada "
            "(art. 49): sin el certificado de la sociedad todo entra como gravado, que es "
            "lo conservador y lo más caro."
        ),
        copy_sugerido=(
            "Hola. Para tu declaración de renta necesito el certificado de dividendos de "
            "{tercero}, con la parte gravada y la no gravada separadas. Mientras no lo "
            "tenga hay que declararlo todo como gravado, y eso te cuesta más impuesto."
        ),
    ),
}

# Los conceptos por los que NO se pide nada, con el porqué de cada uno. Es la otra mitad
# de la partición de `Concepto`:
#   RETENCION            la DIAN ya la reportó: no hay documento que agregue nada.
#   APORTES_SALUD        vienen DENTRO del 220 del empleador, que ya se pide por SALARIOS.
#   APORTES_PENSION      igual.
#   CESANTIAS            igual: son ingreso del año con exención propia (art. 206 num. 4), pero
#                        el papel que las certifica es el mismo 220 del empleador. Pedirlas
#                        aparte le mostraría al contador dos solicitudes del mismo documento.
#   PROMEDIO_SALARIAL    tampoco: NO es plata que se declare —es el insumo del que depende
#                        cuánta cesantía queda exenta— y viaja en ese mismo 220. Si falta, el
#                        motor no calla: levanta CESANTIAS_SIN_PROMEDIO_SALARIAL.
#   HONORARIOS/SERVICIOS/OTROS  el motor no los liquida (CONCEPTOS_FUERA_DEL_MOTOR): el
#                        certificado no los haría entrar al 210, y la salida de esas
#                        partidas es LLEVAR_A_MANO en la cola de pendientes.
#   PATRIMONIO / DEUDA   el saldo al 31 de diciembre que la DIAN publica ES el soporte: el
#                        banco le reportó a la DIAN, no al titular, así que no hay un
#                        certificado que pedirle al cliente por un saldo que ya está
#                        reportado. Lo que SÍ hay que pedirle es lo que la DIAN no ve (el
#                        carro, la casa), y eso no nace de una partida del cruce: nace de
#                        preguntarle, como los beneficios invisibles.
#   SOLO_PARA_TOPE       no se declara en ninguna casilla; sus filas ni abren partida.
_SIN_CERTIFICADO: frozenset[Concepto] = (
    frozenset(
        {
            Concepto.RETENCION,
            Concepto.APORTES_SALUD,
            Concepto.APORTES_PENSION,
            Concepto.CESANTIAS,
            Concepto.PROMEDIO_SALARIAL,
            Concepto.PATRIMONIO,
            Concepto.DEUDA,
            Concepto.SOLO_PARA_TOPE,
            # El saldo a favor arrastrado no tiene certificado que pedir: lo reporta la propia
            # DIAN en la exógena y entra al caso por ahí.
            Concepto.SALDO_FAVOR_ANTERIOR,
        }
    )
    | CONCEPTOS_FUERA_DEL_MOTOR
)


@dataclass
class _Candidata:
    """Una petición antes de saber su puesto: la prioridad es el orden final de la lista,
    así que se asigna al construir el modelo y no con un `model_copy` después."""

    id: str
    tipo_documento: str
    tercero: dict[str, str] | None
    razon: str
    ahorro_estimado: int
    ahorro_es_techo: bool
    ahorro_por_que: str | None
    pregunta_previa: str | None
    copy_sugerido: str


def derivar_peticiones(
    partidas: Sequence[Partida],
    respuestas: Sequence[Respuesta],
    caso: CasoTributario,
    *,
    p: ParametrosAnio | None = None,
    soportes: Sequence[str] = (),
) -> list[Peticion]:
    """La lista priorizada de lo que le falta al expediente.

    `caso` es el caso que hay HOY (el que produce `a_caso` con las partidas resueltas):
    de él sale la base contra la que se mide cada ahorro y qué beneficios ya están
    capturados. `p` se puede inyectar; por defecto se cargan los del año del caso.

    ═══ `soportes` CIERRA EL LAZO QUE ESTABA ABIERTO ═══

    Hasta aca, lo unico que apagaba una peticion era que el beneficio estuviera EN EL CASO, o
    sea que el documento se hubiera podido LEER. Y hay documentos que no se leen: un registro
    civil no es un certificado con cifras, es una prueba de parentesco, y no tiene lector ni
    deberia tenerlo.

    El resultado era el peor posible: el sistema pedia un papel cuya llegada no podia detectar.
    El cliente lo mandaba, quedaba guardado, y la peticion seguia viva pidiendo lo mismo para
    siempre. Con la lista de tipos de documento que YA estan en el expediente, un soporte que
    llego deja de pedirse aunque nadie haya podido extraerle una cifra.

    Que el documento este no significa que el beneficio este aplicado, y esa diferencia NO se
    esconde: al subir un soporte que nadie puede leer se levanta un aviso en el expediente
    diciendo que falta capturar el dato. Cerrar la peticion sin aplicar el beneficio y sin
    decirlo seria cambiar una molestia (pedir dos veces) por un daño (perder plata en
    silencio).
    """
    parametros = p if p is not None else cargar(caso.anio_gravable)
    apagadas = {r.pregunta for r in respuestas if not r.tiene}
    contestadas = {r.pregunta for r in respuestas if r.tiene}
    # Los avisos del cruce viajan a cada estimación: con un bloqueante vivo el optimizador
    # se niega y el ahorro sale como no estimable, en vez de prometer una cifra calculada
    # sobre una base a la que le falta un ingreso (F9, la puerta paralela del bloqueo).
    #
    # `avisos` comparte el ensamble con `a_caso` (una sola fuente de verdad) pero NO su
    # guarda de pendientes, así que sobre un estado intermedio puede negarse con razón: los
    # aportes de un 220 ya resueltos y el salario que los recibe todavía en la cola. Eso no
    # puede tumbar la lista de peticiones — que es justamente lo que hace falta para salir de
    # ese estado —, y no hay liquidación de la que prometer nada mientras el caso no se arme.
    try:
        del_cruce: Sequence[Flag] = avisos(list(partidas))
    except (ValueError, NotImplementedError):
        del_cruce = []

    candidatas = [
        *_de_partidas(partidas, caso, parametros, apagadas, del_cruce),
        *_de_beneficios(caso, parametros, apagadas, contestadas, del_cruce, set(soportes)),
    ]
    # Por plata descendente, con el id como desempate: dos peticiones no estimables (0)
    # tienen que salir siempre en el mismo orden o la lista baila entre consultas.
    candidatas.sort(key=lambda c: (-c.ahorro_estimado, c.id))
    utiles = [c for c in candidatas if c.ahorro_estimado >= UMBRAL_AHORRO or c.ahorro_estimado == 0]
    return [
        Peticion(
            id=c.id,
            tipo_documento=c.tipo_documento,
            tercero=c.tercero,
            razon=c.razon,
            ahorro_estimado=c.ahorro_estimado,
            ahorro_es_techo=c.ahorro_es_techo,
            ahorro_por_que=c.ahorro_por_que,
            prioridad=puesto,
            pregunta_previa=c.pregunta_previa,
            copy_sugerido=c.copy_sugerido,
        )
        for puesto, c in enumerate(utiles[:MAXIMO_PETICIONES], start=1)
    ]


def _de_partidas(
    partidas: Sequence[Partida],
    caso: CasoTributario,
    p: ParametrosAnio,
    apagadas: set[str],
    del_cruce: Sequence[Flag],
) -> list[_Candidata]:
    """Origen 1: cada partida que SOLO sostiene la DIAN y cuyo certificado aporta algo.

    Se mira el ESTADO, no la resolución: una partida SOLO_DIAN con la provisional
    `USAR_DIAN` del sistema sigue necesitando su certificado (ahí están la retención y
    los aportes), y si la provisional apagara la petición nadie volvería a pedir ese 220.

    Una partida ajena (`reportado_a`) no pide nada: es plata de otra persona y pedirle su
    certificado al cliente no tiene sentido.
    """
    candidatas: list[_Candidata] = []
    for partida in partidas:
        if partida.estado is not EstadoPartida.SOLO_DIAN or partida.reportado_a is not None:
            continue
        certificado = _certificado_de(partida.concepto)
        if certificado is None:
            continue
        clave = f"partida:{partida.id}"
        if clave in apagadas:
            continue
        nombre = partida.nombre_tercero or partida.nit_tercero or "el tercero que reportó"
        medida = ahorro_de(caso, _hipotesis_de_partida(caso, partida), p, del_cruce)
        candidatas.append(
            _Candidata(
                id=clave,
                tipo_documento=certificado.tipo_documento,
                tercero={"nit": partida.nit_tercero, "nombre": partida.nombre_tercero},
                razon=certificado.razon,
                ahorro_estimado=medida.pesos,
                ahorro_por_que=medida.por_que,
                # Medido, no techo: los aportes obligatorios son un porcentaje de LEY
                # sobre el pago que la exógena ya reportó.
                ahorro_es_techo=False,
                pregunta_previa=None,
                copy_sugerido=certificado.copy_sugerido.format(tercero=nombre),
            )
        )
    return candidatas


def _certificado_de(concepto: Concepto | None) -> _Certificado | None:
    """El certificado que le falta a un concepto, o None si por ese no se pide nada.

    Un concepto que no esté en NINGUNA de las dos mitades revienta: la tabla de conceptos
    es incremental, y uno nuevo sin clasificar dejaría de pedir su documento EN SILENCIO
    —el mismo defecto que I1 cerró en el ensamble del mapeo—.
    """
    if concepto is None:
        # CONCEPTO_DESCONOCIDO nunca es SOLO_DIAN, así que esto es defensa: sin concepto
        # no se sabe qué documento pedir.
        return None
    if concepto in _SIN_CERTIFICADO:
        return None
    certificado = _CERTIFICADO_POR_CONCEPTO.get(concepto)
    if certificado is None:
        raise NotImplementedError(
            f"No está decidido qué documento se le pide a una partida de {concepto}: "
            "hay que darle un certificado en la tabla o declararlo sin certificado. "
            "Que no se pida nada en silencio no es opción."
        )
    return certificado


def _hipotesis_de_partida(caso: CasoTributario, partida: Partida) -> CasoTributario | None:
    """El caso como quedaría si ese certificado llegara, o None si no se puede afirmar.

    Hoy solo el 220 del asalariado tiene una hipótesis defendible: los aportes
    obligatorios son un porcentaje de ley del pago laboral, no un dato del cliente. Los
    demás certificados traen retención (que no cambia el impuesto, solo el saldo), costos
    o desagregaciones que dependen del documento: se reportan como no estimables.
    """
    if partida.concepto is not Concepto.SALARIOS:
        return None
    laborales: list[IngresoLaboral] = []
    tocado = False
    for laboral in caso.laborales:
        ya_tiene = laboral.aportes_salud or laboral.aportes_pension
        if laboral.fuente.ref == partida.id and not ya_tiene:
            # Las claves son literales del modelo y el test del ahorro fija el resultado:
            # `model_copy(update=...)` no respeta `extra="forbid"` y un typo daría 0.
            laborales.append(
                laboral.model_copy(
                    update={
                        "aportes_salud": porcentaje(laboral.bruto, _APORTE_SALUD_PCT),
                        "aportes_pension": porcentaje(laboral.bruto, _APORTE_PENSION_PCT),
                    }
                )
            )
            tocado = True
        else:
            laborales.append(laboral)
    if not tocado:
        # El hecho de esta partida no está en el caso (todavía sin resolver, o el caso que
        # llegó es otro): no hay nada que mover, así que no hay ahorro que afirmar.
        return None
    return caso.model_copy(update={"laborales": laborales})


def _de_beneficios(
    caso: CasoTributario,
    p: ParametrosAnio,
    apagadas: set[str],
    contestadas: set[str],
    del_cruce: Sequence[Flag],
    soportes: set[str],
) -> list[_Candidata]:
    """Orígenes 2 y 3: los beneficios que la DIAN no puede ver.

    Sin respuesta la petición es una PREGUNTA; con el `sí` dado es la petición del
    certificado; con el `no` no existe (`tiene=False` apaga para siempre). Y si el
    beneficio ya está en el caso, el certificado llegó: no hay nada que pedir.

    Y TAMPOCO SE PIDE LO QUE YA ESTA EN EL EXPEDIENTE, aunque no se haya podido leer: pedirle
    dos veces el mismo papel a alguien que ya lo mandó es la forma más rápida de que deje de
    mandar papeles.
    """
    candidatas: list[_Candidata] = []
    for beneficio in BENEFICIOS:
        if beneficio.pregunta in apagadas or beneficio.presente(caso):
            continue
        if beneficio.tipo_documento in soportes:
            continue
        hipotesis = beneficio.hipotesis(caso, p) if beneficio.hipotesis is not None else None
        medida = ahorro_de(caso, hipotesis, p, del_cruce)
        # El tope se dice en pesos del año. El `copy_sugerido` se le manda al cliente por
        # WhatsApp y una UVT no significa nada para quien lo lee.
        tope = en_pesos(beneficio.tope(p)) if beneficio.tope is not None else ""
        candidatas.append(
            _Candidata(
                id=beneficio.pregunta,
                tipo_documento=beneficio.tipo_documento,
                tercero=None,
                razon=beneficio.razon.format(tope=tope),
                ahorro_estimado=medida.pesos,
                ahorro_por_que=medida.por_que,
                # Todo beneficio invisible se estima en su tope legal: cuánto pagó de
                # prepagada o de intereses lo sabe el cliente, no nosotros.
                ahorro_es_techo=hipotesis is not None,
                pregunta_previa=(
                    None if beneficio.pregunta in contestadas else beneficio.pregunta_previa
                ),
                copy_sugerido=beneficio.copy_sugerido.format(tope=tope),
            )
        )
    return candidatas


def ahorro_de(
    caso: CasoTributario,
    hipotesis: CasoTributario | None,
    p: ParametrosAnio,
    del_cruce: Sequence[Flag] = (),
) -> _Ahorro:
    """Cuánto IMPUESTO ahorraría el documento, y por qué es esa cifra.

    Los pesos son impuesto que se deja de pagar, no reducción de la base gravable: son dos
    números muy distintos y el que le importa a una persona es el primero. Un dependiente baja
    la base en 72 UVT, pero lo que baja el impuesto depende de la tarifa marginal de ESE
    contribuyente, y puede ser cero.

    El segundo elemento es el porqué, y solo viene cuando la cifra no es una medición limpia.
    Sin él, tres situaciones que llevan a decisiones opuestas se ven iguales en pantalla:
    "no baja nada" (no vale la pena molestar al cliente), "no se puede calcular todavía" (hay
    que desbloquear el caso primero) y "es el techo legal, no una medición".

    Un ahorro negativo significaría que la hipótesis SUBE el impuesto, o sea que la
    hipótesis está mal construida: no se muestra como si fuera un costo, se reporta como
    no estimable (0) para no ordenar la lista con un número sin sentido.

    Con un aviso BLOQUEANTE vivo el optimizador se niega (por diseño: no se optimiza sobre
    una base incompleta), y eso NO puede tumbar la lista de peticiones — que es justo lo
    que el contador necesita para salir del bloqueo. Se reporta como no estimable.
    """
    if hipotesis is None:
        return _Ahorro(
            0,
            "no se puede estimar sin inventar cifras: depende de cuánto haya pagado el cliente",
            medido=False,
        )
    try:
        pesos = max(0, ahorro_marginal(caso, hipotesis, p, flags_previos=del_cruce))
    except ValueError:
        # El motor se niega a optimizar sobre una base incompleta. Su mensaje trae el codigo del
        # aviso que bloquea y el porque tecnico, y ninguna de las dos cosas ayuda aqui: el
        # contador ya ve los avisos en el cruce, y lo unico accionable es que primero hay que
        # resolverlos. Volcar el texto del motor filtraba codigos internos a la pantalla.
        # Sin nombrar el cruce, que es vocabulario de contador: lo que importa es que hay algo
        # antes en la fila, y eso vale igual para las dos personas.
        return _Ahorro(0, "todavía no se puede calcular: falta resolver lo de arriba", medido=False)
    except NotImplementedError:
        # El caso no se puede armar (p. ej. ingresos de independientes, fuera del alcance).
        # Reportar 0 sin decirlo haria pensar que el beneficio no sirve, cuando lo que pasa es
        # que todavia no hay con que medirlo.
        return _Ahorro(
            0, "no se puede calcular: el cálculo todavía no cubre este caso", medido=False
        )

    if pesos:
        return _Ahorro(pesos, None, medido=True)

    # Cero medido: el beneficio existe y NO baja el impuesto. Hay que decir por que, porque la
    # conclusion practica es distinta —no vale la pena pedirle el documento al cliente— y sin la
    # razon parece una falla del calculo.
    try:
        impuesto = optimizar(caso, p, flags_previos=del_cruce).liquidacion.valor("IMPUESTO_NETO")
    except (ValueError, NotImplementedError):
        return _Ahorro(0, "no se puede calcular todavía", medido=False)
    if impuesto == 0:
        return _Ahorro(
            0,
            "no baja nada: con lo que ya hay registrado no queda impuesto que bajar",
            medido=True,
        )
    return _Ahorro(
        0,
        (
            "no baja nada: lo que ya está registrado copa el límite legal de deducciones, "
            "así que una más no mueve el impuesto"
        ),
        medido=True,
    )


def costo_de_cerrar(peticion: Peticion) -> int:
    """Lo que cuesta cerrar una petición sin soporte: el ahorro que se deja de tener.

    Es la misma cifra que `ahorro_estimado` —la diferencia de liquidar con y sin ese
    documento— y no un cálculo aparte: dos fórmulas para el mismo número acabarían
    diciendo cosas distintas en la misma pantalla.
    """
    return peticion.ahorro_estimado


__all__ = [
    "MAXIMO_PETICIONES",
    "UMBRAL_AHORRO",
    "Peticion",
    "costo_de_cerrar",
    "derivar_peticiones",
]


def beneficio_de_documento(doc_type: str) -> str | None:
    """Que beneficio soporta ese tipo de documento, en palabras, o None si no soporta ninguno.

    Sirve para decir QUE hay que capturar cuando llega un soporte que nadie puede leer. Sale
    del mismo catalogo que las peticiones: si un beneficio cambia de documento, esto lo sigue
    solo, sin una segunda tabla que se desactualice en silencio.
    """
    for beneficio in BENEFICIOS:
        if beneficio.tipo_documento == doc_type:
            return etiqueta_de_pregunta(beneficio.pregunta)
    return None
