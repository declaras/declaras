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

from declaras.caso import (
    Beneficios,
    CasoTributario,
    Dependiente,
    Fuente,
    IngresoLaboral,
    MontoDeclarado,
)
from declaras.dinero import porcentaje
from declaras.motor import Flag
from declaras.optimizador import ahorro_marginal
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
    prioridad: int
    pregunta_previa: str | None
    copy_sugerido: str


# ─────────────────────────── el catálogo de beneficios invisibles ───────────────────────────
#
# "Invisible" es literal: no aparece en la exógena ni en ningún documento que el portal
# entregue, así que si nadie pregunta, esa plata se pierde. El catálogo es la razón de ser
# del producto y por eso los textos viven acá, en una tabla, y no improvisados por llamada.


@dataclass(frozen=True)
class _Beneficio:
    """Un beneficio que hay que preguntar, con su copy y su hipótesis de ahorro."""

    pregunta: str
    tipo_documento: str
    razon: str
    pregunta_previa: str
    copy_sugerido: str
    # ¿Ya está capturado en el caso? Si sí, el certificado llegó y no hay nada que pedir.
    presente: Callable[[Beneficios], bool]
    # El caso con el beneficio en su TECHO legal, o None cuando el techo no se puede
    # afirmar sin inventar plata del cliente (ahí el ahorro se reporta como no estimable).
    # Todo lo que salga de acá es un techo, nunca una medición: lo marca `ahorro_es_techo`.
    hipotesis: Callable[[CasoTributario, ParametrosAnio], CasoTributario] | None


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
}


def etiqueta_de_pregunta(pregunta: str) -> str:
    """Nombre legible de una pregunta; si es una derivada del cruce, su propia clave."""
    return ETIQUETAS_DE_PREGUNTA.get(pregunta, pregunta.replace("_", " ").lower())


_BENEFICIOS: tuple[_Beneficio, ...] = (
    _Beneficio(
        pregunta="PREPAGADA",
        tipo_documento="CERT_PREPAGADA",
        razon=(
            "La medicina prepagada es deducible hasta 16 UVT al mes y la DIAN no la ve: "
            "sin el certificado de la aseguradora esa plata no entra al 210."
        ),
        pregunta_previa="¿Pagaste medicina prepagada o un plan complementario de salud?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: ¿tuviste medicina prepagada o plan "
            "complementario de salud? Si sí, mándame el certificado anual que emite la "
            "aseguradora (Colsanitas, Sura, Coomeva, Medplus...). Es una deducción que la "
            "DIAN no ve sola y puede bajarte bastante el impuesto."
        ),
        presente=lambda b: b.medicina_prepagada is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, medicina_prepagada=_monto(p.uvt_pesos(p.prepagada_tope_uvt_anio))
        ),
    ),
    _Beneficio(
        pregunta="DEPENDIENTES",
        tipo_documento="SOPORTE_DEPENDIENTE",
        razon=(
            "Cada dependiente vale 72 UVT por fuera del límite del 40%, y ningún tercero "
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
        presente=lambda b: bool(b.dependientes),
        hipotesis=lambda caso, p: _con_beneficios(
            caso, dependientes=[Dependiente(tipo="hijo_menor", fuente=_FUENTE_HIPOTESIS)]
        ),
    ),
    _Beneficio(
        pregunta="INTERESES_VIVIENDA",
        tipo_documento="CERT_INTERESES_VIVIENDA",
        razon=(
            "Los intereses del crédito de vivienda son deducibles hasta 1.200 UVT; el "
            "banco los certifica una vez al año y la exógena no los trae desagregados."
        ),
        pregunta_previa="¿Tienes crédito de vivienda o leasing habitacional?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si tienes crédito de vivienda o leasing "
            "habitacional, mándame el certificado de intereses del año que emite el banco "
            "(lo descargas desde la banca en línea). Los intereses son deducibles hasta "
            "1.200 UVT."
        ),
        presente=lambda b: b.intereses_vivienda is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, intereses_vivienda=_monto(p.uvt_pesos(p.intereses_vivienda_tope_uvt))
        ),
    ),
    _Beneficio(
        pregunta="ICETEX",
        tipo_documento="CERT_ICETEX",
        razon=(
            "Los intereses de un crédito educativo del ICETEX son deducibles hasta "
            "100 UVT y solo constan en el certificado de la entidad."
        ),
        pregunta_previa="¿Pagaste intereses de un crédito educativo del ICETEX?",
        copy_sugerido=(
            "Hola. Para tu declaración de renta: si tienes crédito educativo con el "
            "ICETEX, mándame el certificado de intereses del año. Son deducibles hasta "
            "100 UVT."
        ),
        presente=lambda b: b.intereses_icetex is not None,
        hipotesis=lambda caso, p: _con_beneficios(
            caso, intereses_icetex=_monto(p.uvt_pesos(p.icetex_tope_uvt))
        ),
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
        presente=lambda b: bool(b.aportes_afc_fvp),
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
        presente=lambda b: bool(b.donaciones_esal),
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
        presente=lambda b: b.gmf_pagado is not None,
        # Sin hipótesis: depende de cuánto se movió en las cuentas.
        hipotesis=None,
    ),
)


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
#   HONORARIOS/SERVICIOS/OTROS  el motor no los liquida (CONCEPTOS_FUERA_DEL_MOTOR): el
#                        certificado no los haría entrar al 210, y la salida de esas
#                        partidas es LLEVAR_A_MANO en la cola de pendientes.
_SIN_CERTIFICADO: frozenset[Concepto] = (
    frozenset({Concepto.RETENCION, Concepto.APORTES_SALUD, Concepto.APORTES_PENSION})
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
    pregunta_previa: str | None
    copy_sugerido: str


def derivar_peticiones(
    partidas: Sequence[Partida],
    respuestas: Sequence[Respuesta],
    caso: CasoTributario,
    *,
    p: ParametrosAnio | None = None,
) -> list[Peticion]:
    """La lista priorizada de lo que le falta al expediente.

    `caso` es el caso que hay HOY (el que produce `a_caso` con las partidas resueltas):
    de él sale la base contra la que se mide cada ahorro y qué beneficios ya están
    capturados. `p` se puede inyectar; por defecto se cargan los del año del caso.
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
        *_de_beneficios(caso, parametros, apagadas, contestadas, del_cruce),
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
        candidatas.append(
            _Candidata(
                id=clave,
                tipo_documento=certificado.tipo_documento,
                tercero={"nit": partida.nit_tercero, "nombre": partida.nombre_tercero},
                razon=certificado.razon,
                ahorro_estimado=_ahorro(caso, _hipotesis_de_partida(caso, partida), p, del_cruce),
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
) -> list[_Candidata]:
    """Orígenes 2 y 3: los beneficios que la DIAN no puede ver.

    Sin respuesta la petición es una PREGUNTA; con el `sí` dado es la petición del
    certificado; con el `no` no existe (`tiene=False` apaga para siempre). Y si el
    beneficio ya está en el caso, el certificado llegó: no hay nada que pedir.
    """
    candidatas: list[_Candidata] = []
    for beneficio in _BENEFICIOS:
        if beneficio.pregunta in apagadas or beneficio.presente(caso.beneficios):
            continue
        hipotesis = beneficio.hipotesis(caso, p) if beneficio.hipotesis is not None else None
        candidatas.append(
            _Candidata(
                id=beneficio.pregunta,
                tipo_documento=beneficio.tipo_documento,
                tercero=None,
                razon=beneficio.razon,
                ahorro_estimado=_ahorro(caso, hipotesis, p, del_cruce),
                # Todo beneficio invisible se estima en su tope legal: cuánto pagó de
                # prepagada o de intereses lo sabe el cliente, no nosotros.
                ahorro_es_techo=hipotesis is not None,
                pregunta_previa=(
                    None if beneficio.pregunta in contestadas else beneficio.pregunta_previa
                ),
                copy_sugerido=beneficio.copy_sugerido,
            )
        )
    return candidatas


def _ahorro(
    caso: CasoTributario,
    hipotesis: CasoTributario | None,
    p: ParametrosAnio,
    del_cruce: Sequence[Flag] = (),
) -> int:
    """Cuánto impuesto ahorraría el documento, o 0 si no se puede estimar.

    Un ahorro negativo significaría que la hipótesis SUBE el impuesto, o sea que la
    hipótesis está mal construida: no se muestra como si fuera un costo, se reporta como
    no estimable (0) para no ordenar la lista con un número sin sentido.

    Con un aviso BLOQUEANTE vivo el optimizador se niega (por diseño: no se optimiza sobre
    una base incompleta), y eso NO puede tumbar la lista de peticiones — que es justo lo
    que el contador necesita para salir del bloqueo. Se reporta como no estimable.
    """
    if hipotesis is None:
        return 0
    try:
        return max(0, ahorro_marginal(caso, hipotesis, p, flags_previos=del_cruce))
    except ValueError:
        return 0


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
