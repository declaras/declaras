"""El mapeo: de partidas resueltas al `CasoTributario` que el motor liquida.

`a_caso` es la frontera entre el conciliador y el motor: solo cruzan las partidas cuya
resolución APORTA hecho (USAR_DIAN, USAR_DOCUMENTO, USAR_OTRO — las provisionales del
sistema incluidas: son el 210 preliminar), cada una con su proveniencia
`Fuente.conciliacion(partida.id, ...)` para poder volver del hecho a la discrepancia que
lo originó. MARCAR_AJENO y CERRAR_SIN_SOPORTE cierran la partida sin aportar nada.

EL ENSAMBLE ES POR TERCERO (NIT), no por concepto: en la exógena los salarios, los
aportes y la retención de un mismo empleador son filas distintas, y `IngresoLaboral` los
exige juntos. Lo que el ensamble asume sin poder verificarlo queda en `avisos` — la misma
`Flag` del motor, para que quien liquide las fusione en la `Liquidacion` y salgan
impresas en el borrador (el motor y `caso/` no se tocan en esta tarea, así que el aviso
no puede nacer adentro de ellos).

LO QUE EL ENSAMBLE NO PUEDE VER, para quien lo llame: dos partidas con ids distintos
pueden ser la misma plata (la suelta `sin-nit:...` y la conciliada del mismo empleador,
forma aceptada en T4). Si una persona resuelve LAS DOS con hecho, acá entran como dos
hechos — la salida sin doble conteo es cerrar la suelta con CERRAR_SIN_SOPORTE.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from declaras.caso import (
    Arriendo,
    Beneficios,
    CasoTributario,
    Contribuyente,
    Creditos,
    Dividendo,
    Fuente,
    IngresoLaboral,
    IngresoPension,
    Patrimonio,
    Rendimiento,
)
from declaras.motor import Flag
from declaras.services.conciliacion.conceptos import CONCEPTOS_FUERA_DEL_MOTOR, Concepto
from declaras.services.conciliacion.modelos import Decision, Partida, Resolucion, Valor
from declaras.services.conciliacion.resolucion import pendientes

# Las decisiones que hacen valer un monto en el caso; las otras tres cierran sin aportar.
DECISIONES_CON_HECHO = frozenset({Decision.USAR_DIAN, Decision.USAR_DOCUMENTO, Decision.USAR_OTRO})

# Códigos de los avisos del ensamble (los lee T6 al fusionarlos en la liquidación).
PENSION_DISTRIBUIDA_UNIFORME = "PENSION_DISTRIBUIDA_UNIFORME"
DIVIDENDOS_SIN_DESAGREGAR = "DIVIDENDOS_SIN_DESAGREGAR"
RETENCION_SIN_INGRESO = "RETENCION_SIN_INGRESO"
INGRESO_LLEVADO_A_MANO = "INGRESO_LLEVADO_A_MANO"
RETENCION_DESPLAZADA = "RETENCION_DESPLAZADA"
POSIBLE_DOBLE_CONTEO = "POSIBLE_DOBLE_CONTEO"
INGRESO_EXCLUIDO = "INGRESO_EXCLUIDO"

# Orden de ensamble por tercero. También es la prioridad con que la retención explícita
# (las filas R132 del tercero) se asigna a UN ingreso: el laboral primero.
_ORDEN_INGRESOS = (
    Concepto.SALARIOS,
    Concepto.PENSIONES,
    Concepto.RENDIMIENTOS,
    Concepto.ARRENDAMIENTOS,
    Concepto.DIVIDENDOS,
)


def a_caso(
    partidas: list[Partida],
    *,
    contribuyente: Contribuyente,
    anio_gravable: int,
    beneficios: Beneficios | None = None,
    patrimonio: Patrimonio | None = None,
    creditos: Creditos | None = None,
) -> CasoTributario:
    """Las partidas resueltas, convertidas en el caso que `liquidar` recibe.

    Exige que NO queden pendientes: una partida sin resolución es una pregunta abierta y
    liquidar por encima de ella escondería plata (o la inventaría). Los `beneficios`, el
    `patrimonio` y los `creditos` no salen del cruce — vienen de la captura y de otras
    fuentes — y entran tal cual.
    """
    sin_resolver = pendientes(partidas)
    if sin_resolver:
        n = len(sin_resolver)
        raise ValueError(
            f"Queda{'n' if n != 1 else ''} {n} partida{'s' if n != 1 else ''} sin "
            "resolver: el caso solo se arma cuando todas están decididas."
        )
    ensamble = _ensamblar(partidas)
    return CasoTributario(
        anio_gravable=anio_gravable,
        contribuyente=contribuyente,
        laborales=ensamble.laborales,
        pensiones=ensamble.pensiones,
        rendimientos=ensamble.rendimientos,
        arriendos=ensamble.arriendos,
        dividendos=ensamble.dividendos,
        beneficios=beneficios if beneficios is not None else Beneficios(),
        patrimonio=patrimonio if patrimonio is not None else Patrimonio(),
        creditos=creditos if creditos is not None else Creditos(),
    )


def avisos(partidas: list[Partida]) -> list[Flag]:
    """Lo que el ensamble asumió o excluyó y el contador tiene que ver en el borrador.

    Sale del MISMO ensamble que `a_caso` (una sola fuente de verdad) como `Flag` del
    motor: quien liquide las fusiona en `Liquidacion.flags` y el render las imprime en
    la sección de alertas.
    """
    return _ensamblar(partidas).avisos


@dataclass
class _Ensamble:
    laborales: list[IngresoLaboral] = field(default_factory=list)
    pensiones: list[IngresoPension] = field(default_factory=list)
    rendimientos: list[Rendimiento] = field(default_factory=list)
    arriendos: list[Arriendo] = field(default_factory=list)
    dividendos: list[Dividendo] = field(default_factory=list)
    avisos: list[Flag] = field(default_factory=list)


def _ensamblar(partidas: list[Partida]) -> _Ensamble:
    ids = [p.id for p in partidas]
    if len(set(ids)) != len(ids):
        # I3 de la ronda 2: el id es la identidad del hecho — la misma partida dos
        # veces (una lista persistida y reensamblada mal, T6) duplicaba ingresos y
        # retención sin aviso. El cruce garantiza ids únicos; acá se exige.
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(
            f"Hay partidas con el id repetido: {', '.join(repetidos)}. Cada hecho "
            "entra al caso una sola vez; una lista con duplicados duplicaría la plata."
        )
    ensamble = _Ensamble()
    grupos: dict[str, list[Partida]] = {}
    for p in partidas:
        if p.resolucion is None:
            continue
        if p.resolucion.decision is Decision.LLEVAR_A_MANO:
            # Ruling de la ronda de fixes 1: la partida sale de la liquidación para que
            # el contador la sume a mano — pero excluir un ingreso es subdeclarar, así
            # que la exclusión JAMÁS es silenciosa (tercero, concepto y cifra) ni
            # informativa (BLOQUEANTE: la liquidación queda marcada incompleta y nadie
            # presenta ese 210 creyéndolo completo).
            ensamble.avisos.append(_aviso_llevada_a_mano(p))
            continue
        if p.resolucion.decision not in DECISIONES_CON_HECHO:
            # I7 de la ronda 2, ruling: "la exclusión jamás silenciosa" aplica a las
            # TRES decisiones sin hecho, no solo a LLEVAR_A_MANO. MARCAR_AJENO y
            # CERRAR_SIN_SOPORTE eran la puerta paralela — plata fuera del 210 con
            # avisos() vacío. Informativo, no bloqueante: acá una persona afirmó que la
            # plata NO va ("no es mío" / "sin soporte"), que es más fuerte que "al
            # motor le falta el concepto"; el borrador enumera lo excluido igual.
            ensamble.avisos.append(_aviso_exclusion(p))
            continue
        if p.concepto is None:
            # Defensa contra partidas construidas a mano: la tabla de decisiones ya
            # impide resolver una CONCEPTO_DESCONOCIDO con hecho, pero `Partida` no
            # valida coherencia entre campos.
            raise ValueError(
                "Una partida resuelta sin concepto clasificado no puede aportar un "
                "hecho: no se sabe a qué renglón del 210 iría su valor."
            )
        grupos.setdefault(_tercero(p), []).append(p)
    _avisar_posible_doble_conteo(ensamble, grupos)
    for del_tercero in grupos.values():
        _ensamblar_tercero(ensamble, del_tercero)
    return ensamble


def _avisar_posible_doble_conteo(ensamble: _Ensamble, grupos: dict[str, list[Partida]]) -> None:
    """I6 de la ronda 2: la suelta sin NIT y la conciliada del mismo empleador pueden ser
    la misma plata (forma aceptada en T4), y si una persona resuelve LAS DOS con hecho
    entran como dos hechos — ingresos Y retención dobles. El ensamble no puede saberlo
    con certeza (ids distintos, nada las liga), pero es el único punto donde se ven todas
    las resueltas juntas: mismo concepto + misma cifra + mismo nombre, una con NIT y otra
    sin, es la heurística barata que lo delata. La salida documentada es
    CERRAR_SIN_SOPORTE sobre la suelta, que apaga este aviso.
    """
    hechos = [p for grupo in grupos.values() for p in grupo]
    sueltas = [p for p in hechos if not p.nit_tercero]
    identificadas = [p for p in hechos if p.nit_tercero]
    for suelta in sueltas:
        for identificada in identificadas:
            mismo_nombre = (
                suelta.nombre_tercero
                and identificada.nombre_tercero
                and suelta.nombre_tercero.casefold() == identificada.nombre_tercero.casefold()
            )
            if (
                suelta.concepto is identificada.concepto
                and mismo_nombre
                and _resuelta(suelta).valor == _resuelta(identificada).valor
            ):
                ensamble.avisos.append(
                    Flag(
                        codigo=POSIBLE_DOBLE_CONTEO,
                        mensaje=(
                            f"Las partidas {suelta.id} y {identificada.id} entraron las "
                            f"dos al caso con el mismo concepto, el mismo nombre "
                            f"({identificada.nombre_tercero}) y la misma cifra "
                            f"({_resuelta(identificada).valor:,} pesos): pueden ser el "
                            "mismo hecho contado dos veces. Si lo son, cerrar la suelta "
                            "sin NIT con CERRAR_SIN_SOPORTE."
                        ),
                    )
                )


def _tercero(p: Partida) -> str:
    """La llave del grupo: el NIT, o la identidad que el id lleva cuando no hay NIT.

    Sin NIT, las partidas del MISMO origen comparten prefijo de id (`sin-nit:{doc_type}`
    para las sueltas de un documento, `nombre:{nombre}` para las filas de exógena): los
    tres hechos del mismo 220 sin NIT arman UN laboral, no un laboral sin aportes más
    aportes huérfanos. Se quita del final lo que no es identidad (concepto y sufijo de
    ajena), nunca se parsea del principio: un nombre con dos puntos no rompe la llave.
    """
    if p.nit_tercero:
        return p.nit_tercero
    identidad = p.id
    if p.reportado_a is not None:
        identidad = identidad.removesuffix(f":reportado-a:{p.reportado_a}")
    if p.concepto is not None:
        identidad = identidad.removesuffix(f":{p.concepto}")
    return identidad


def _ensamblar_tercero(ensamble: _Ensamble, partidas: list[Partida]) -> None:
    por_concepto: dict[Concepto, list[Partida]] = {}
    # Titular primero y luego por id: con gemelas (titular + ajena reclamada por el
    # contador) los aportes y la retención explícita se asignan al titular, no al
    # orden de llegada del XLSX.
    for p in sorted(partidas, key=lambda x: (x.reportado_a is not None, x.id)):
        assert p.concepto is not None  # filtrado en _ensamblar
        por_concepto.setdefault(p.concepto, []).append(p)

    sin_modelo = sorted(str(c) for c in por_concepto if c in CONCEPTOS_FUERA_DEL_MOTOR)
    if sin_modelo:
        # El backstop honesto y ruidoso del brief: un hecho de estos conceptos no tiene
        # modelo y silenciarlo haría desaparecer una cédula completa. La salida buena
        # está una capa antes: resolver la partida con LLEVAR_A_MANO (ruling de la
        # ronda de fixes 1), que excluye con aviso bloqueante en vez de tronar.
        raise NotImplementedError(
            f"El caso tributario todavía no modela estos conceptos: "
            f"{', '.join(sin_modelo)}. El motor no cubre la cédula de independientes; "
            "la salida es resolver esas partidas con LLEVAR_A_MANO y sumarlas a mano."
        )

    # LA RETENCIÓN DEL TERCERO VIVE EN DOS SITIOS (herencia de T4, medida): la partida
    # RETENCION (filas que la DIAN asignó al renglón 132) y la retención que afirma la
    # versión escogida del ingreso (la casilla del 220). Son el mismo hecho contado dos
    # veces: rige UNA fuente y NUNCA se suman — sumarlas declara 16M donde hay 8M y el
    # saldo a favor queda inflado. Manda la partida explícita, que es el renglón que la
    # propia DIAN asignó (declarar más retención que lo reportado por el tercero casi
    # garantiza requerimiento); sin ella, la de la versión escogida.
    retenciones = por_concepto.pop(Concepto.RETENCION, [])
    retencion_pendiente = sum(_resuelta(p).valor for p in retenciones) if retenciones else None

    salud = por_concepto.pop(Concepto.APORTES_SALUD, [])
    pension_obligatoria = por_concepto.pop(Concepto.APORTES_PENSION, [])
    if (salud or pension_obligatoria) and Concepto.SALARIOS not in por_concepto:
        # El horror documentado en T4: un IngresoLaboral con 0 de salario y los aportes
        # completos. Aportes con hecho y salario sin hecho es una contradicción entre
        # resoluciones, no un caso que armar en silencio.
        raise ValueError(
            "Hay aportes obligatorios resueltos sin un ingreso laboral del mismo "
            "tercero que los reciba: hay que revisar esas resoluciones."
        )

    # El cierre de la partición (I1 de la ronda 2): a esta altura todo concepto tiene
    # dueño — RETENCION y los aportes se consumieron arriba, los fuera-del-motor ya
    # reventaron, y lo que queda debe estar en `_ORDEN_INGRESOS`. La tabla de conceptos
    # es INCREMENTAL: uno nuevo que nadie agregue ni acá ni a CONCEPTOS_FUERA_DEL_MOTOR
    # se caía del caso EN SILENCIO (20M resueltos → brutos 0, sin excepción ni aviso).
    sin_ensamble = sorted(str(c) for c in por_concepto if c not in _ORDEN_INGRESOS)
    if sin_ensamble:
        raise NotImplementedError(
            f"Hay conceptos resueltos que el ensamble no sabe llevar al caso: "
            f"{', '.join(sin_ensamble)}. Hay que mapearlos a un modelo del caso o "
            "declararlos fuera del alcance del motor; que desaparezcan no es opción."
        )

    for concepto in _ORDEN_INGRESOS:
        for indice, p in enumerate(por_concepto.get(concepto, [])):
            retencion = _retencion_afirmada(p)
            extras = []
            if retencion_pendiente is not None:
                if retencion not in (0, retencion_pendiente):
                    # I4 de la ronda 2: la prioridad de la fuente explícita se mantiene
                    # (ratificada), pero desplazar EN SILENCIO una retención certificada
                    # DISTINTA esconde plata — el contador tiene que ver las dos cifras
                    # y decidir cuál es la real. Cubre también el 0 explícito de
                    # USAR_OTRO sobre la partida RETENCION, indistinguible de "no hay
                    # fuente explícita" sin este aviso.
                    nombre = p.nombre_tercero or p.nit_tercero
                    ensamble.avisos.append(
                        Flag(
                            codigo=RETENCION_DESPLAZADA,
                            mensaje=(
                                f"La retención declarada para {nombre} salió de la fuente "
                                f"explícita ({retencion_pendiente:,} pesos, "
                                f"{', '.join(x.id for x in retenciones)}) y desplazó la "
                                f"que certifica la otra versión ({retencion:,} pesos): "
                                "rige una sola fuente — verificar cuál es la real antes "
                                "de presentar."
                            ),
                        )
                    )
                retencion = retencion_pendiente
                retencion_pendiente = None
                extras.append(f"retención de {', '.join(x.id for x in retenciones)}")
            if concepto is Concepto.SALARIOS:
                aportes = (salud, pension_obligatoria) if indice == 0 else ([], [])
                if any(aportes):
                    ids = [x.id for x in aportes[0] + aportes[1]]
                    extras.append(f"aportes de {', '.join(ids)}")
                ensamble.laborales.append(
                    IngresoLaboral(
                        empleador_nit=p.nit_tercero,
                        empleador_nombre=p.nombre_tercero,
                        # El valor resuelto es el agregado de pagos laborales (el 5001 de la
                        # exógena y el lado documento del 220 agregan igual): va completo en
                        # `salarios` y el motor solo consume `bruto`, que es su suma.
                        salarios=_resuelta(p).valor,
                        aportes_salud=sum(_resuelta(x).valor for x in aportes[0]),
                        aportes_pension=sum(_resuelta(x).valor for x in aportes[1]),
                        retencion=retencion,
                        fuente=_fuente(p, extras),
                    )
                )
            elif concepto is Concepto.PENSIONES:
                pagador = p.nombre_tercero or p.nit_tercero
                total = _resuelta(p).valor
                ensamble.pensiones.append(
                    IngresoPension(
                        pagador=pagador,
                        mesadas=_mesadas(total),
                        retencion=retencion,
                        fuente=_fuente(p, extras),
                    )
                )
                ensamble.avisos.append(
                    Flag(
                        codigo=PENSION_DISTRIBUIDA_UNIFORME,
                        mensaje=(
                            f"La pensión de {pagador} entró como el total anual "
                            f"({total:,} pesos) repartido en 12 mesadas iguales: correcto "
                            "si la mesada fue pareja, equivocado si hubo retroactivos o "
                            "reajustes — verificar contra los comprobantes del pagador."
                        ),
                    )
                )
            elif concepto is Concepto.RENDIMIENTOS:
                ensamble.rendimientos.append(
                    Rendimiento(
                        entidad=p.nombre_tercero or p.nit_tercero,
                        valor=_resuelta(p).valor,
                        retencion=retencion,
                        fuente=_fuente(p, extras),
                    )
                )
            elif concepto is Concepto.ARRENDAMIENTOS:
                ensamble.arriendos.append(
                    Arriendo(
                        # La exógena identifica a quien PAGÓ el canon, no al inmueble; los
                        # costos (predial, administración...) tampoco salen del cruce y
                        # entran después por la captura.
                        inmueble=p.nombre_tercero or p.nit_tercero,
                        canon_total=_resuelta(p).valor,
                        retencion=retencion,
                        fuente=_fuente(p, extras),
                    )
                )
            elif concepto is Concepto.DIVIDENDOS:
                nombre = p.nombre_tercero or p.nit_tercero
                ensamble.dividendos.append(
                    Dividendo(
                        sociedad_nit=p.nit_tercero,
                        sociedad_nombre=p.nombre_tercero,
                        # La partida trae UN número y el modelo exige el desglose. Se asume
                        # GRAVADOS — la dirección que nunca subdeclara, como el componente
                        # inflacionario en 0% — y el aviso deja la decisión a la vista.
                        gravados=_resuelta(p).valor,
                        no_gravados=0,
                        retencion=retencion,
                        fuente=_fuente(p, extras),
                    )
                )
                ensamble.avisos.append(
                    Flag(
                        codigo=DIVIDENDOS_SIN_DESAGREGAR,
                        mensaje=(
                            f"Los dividendos de {nombre} entraron completos como gravados: "
                            "la partida no distingue la parte no gravada (art. 49) y "
                            "asumirla sin soporte bajaría el impuesto. Con el certificado "
                            "de la sociedad se desagregan y la carga puede bajar."
                        ),
                    )
                )

    if retencion_pendiente is not None:
        # Declararla sola fabrica un saldo a favor sin ingreso que lo sostenga;
        # perderla en silencio regala plata del cliente. No entra, y queda a la vista.
        nombre = retenciones[0].nombre_tercero or retenciones[0].nit_tercero
        ensamble.avisos.append(
            Flag(
                codigo=RETENCION_SIN_INGRESO,
                mensaje=(
                    f"La retención reportada por {nombre} ({retencion_pendiente:,} pesos) "
                    "quedó resuelta sin ningún ingreso del mismo tercero en el caso: no se "
                    "declaró, porque una retención sin ingreso fabrica un saldo a favor "
                    "sin sustento. Revisar de qué ingreso viene."
                ),
            )
        )


def _aviso_exclusion(p: Partida) -> Flag:
    """El aviso informativo de una partida cerrada sin hecho: qué quedó por fuera del
    210 y por qué. Enumera partida, tercero, concepto y cifra — la lista de exclusiones
    que el contador repasa antes de presentar."""
    res = _resuelta(p)
    nombre = p.nombre_tercero or p.nit_tercero or "un tercero sin identificar"
    concepto = str(p.concepto) if p.concepto is not None else "concepto sin clasificar"
    return Flag(
        codigo=INGRESO_EXCLUIDO,
        severidad="info",
        mensaje=(
            f"La partida {p.id} de {nombre} ({concepto}, {_cifras_conocidas(p)}) quedó "
            f"por fuera del 210 por decisión de {res.quien}: {res.decision} "
            f"({res.motivo})."
        ),
    )


def _aviso_llevada_a_mano(p: Partida) -> Flag:
    """El aviso BLOQUEANTE de un ingreso que el contador va a sumar a mano.

    Dice el tercero, el concepto y LA CIFRA (las dos, si las versiones difieren): un
    aviso genérico de "hay conceptos fuera de alcance" no le dice al contador qué le
    toca sumar, y la exclusión volvería a ser efectivamente silenciosa.
    """
    nombre = p.nombre_tercero or p.nit_tercero or "un tercero sin identificar"
    return Flag(
        codigo=INGRESO_LLEVADO_A_MANO,
        severidad="bloqueante",
        mensaje=(
            f"El ingreso de {nombre} por {p.concepto} quedó POR FUERA de esta "
            f"liquidación ({_cifras_conocidas(p)}): el motor todavía no cubre ese "
            "concepto y hay que sumarlo a mano en el 210. Este borrador está "
            "incompleto mientras ese ingreso no esté incorporado."
        ),
    )


def _cifras_conocidas(p: Partida) -> str:
    """La cifra que se conoce de la partida, citando el lado que la afirma."""
    dian = p.version_dian.monto if p.version_dian is not None else None
    documento = p.version_documento.monto if p.version_documento is not None else None
    if dian is not None and documento is not None and dian != documento:
        return f"{dian:,} pesos según la DIAN, {documento:,} según el documento"
    cifra = dian if dian is not None else documento
    return f"{cifra:,} pesos" if cifra is not None else "sin cifra registrada"


def _resuelta(p: Partida) -> Resolucion:
    resolucion = p.resolucion
    assert resolucion is not None  # _ensamblar solo agrupa partidas resueltas con hecho
    return resolucion


def _version_escogida(p: Partida) -> Valor | None:
    """La versión de la que salió el valor resuelto; None con USAR_OTRO (el número lo
    puso el contador, no un documento ni la exógena)."""
    decision = _resuelta(p).decision
    if decision is Decision.USAR_DIAN:
        return p.version_dian
    if decision is Decision.USAR_DOCUMENTO:
        return p.version_documento
    return None


def _retencion_afirmada(p: Partida) -> int:
    """La retención que afirma la versión escogida — y si ESA no la afirma, la única
    afirmación que exista en la partida.

    C2 de la ronda de fixes 2: la decisión del contador es sobre el MONTO. Con la
    exógena real (sin columna de retención → el lado DIAN no la afirma; None NO es 0,
    invariante de T4) y un 220 con retención certificada, USAR_DIAN declaraba 0 y el
    crédito se perdía sin aviso ni partida RETENCION donde recuperarlo. Caer a la
    afirmación de la otra versión sigue rigiendo UNA fuente (nunca una suma): un 0
    AFIRMADO por la escogida se respeta tal cual — solo la ausencia (None) cae. El
    orden del respaldo pone primero el documento: es el certificado que soporta el
    crédito ante la DIAN, y es también la única opción determinística con USAR_OTRO,
    donde ninguna versión fue escogida.
    """
    escogida = _version_escogida(p)
    if escogida is not None and escogida.retencion is not None:
        return escogida.retencion
    for version in (p.version_documento, p.version_dian):
        if version is not None and version.retencion is not None:
            return version.retencion
    return 0


def _mesadas(total: int) -> list[int]:
    """El total anual en 12 mesadas que SUMAN exacto: división entera con el resto
    repartido peso a peso en los primeros meses. Acá no hay redondeo que cerrar por
    `dinero.pesos` — redondear total/12 perdería o inventaría pesos."""
    base, resto = divmod(total, 12)
    return [base + 1 if mes < resto else base for mes in range(12)]


def _fuente(p: Partida, extras: list[str]) -> Fuente:
    """La proveniencia del hecho: la partida (para volver a la discrepancia), quién y
    cómo la resolvió, y la procedencia fina (celda, confianza) de la versión escogida."""
    res = _resuelta(p)
    detalle = f"{res.decision} ({res.motivo}) por {res.quien}"
    if extras:
        detalle += "; " + "; ".join(extras)
    fuente = Fuente.conciliacion(p.id, detalle)
    version = _version_escogida(p)
    if version is None:
        return fuente
    # `Fuente.conciliacion` (contrato de T2) no recibe celda ni confianza y `caso/` está
    # congelado en esta tarea: se adjuntan con `model_copy` sobre claves LITERALES del
    # modelo (la trampa de `model_copy(update=...)` es el typo silencioso; acá las claves
    # están a la vista y los tests fijan los valores).
    return fuente.model_copy(update={"celda": version.celda, "confianza": version.confianza})
