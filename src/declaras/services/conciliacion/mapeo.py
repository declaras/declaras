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
from declaras.services.conciliacion.conceptos import Concepto
from declaras.services.conciliacion.modelos import Decision, Partida, Resolucion, Valor
from declaras.services.conciliacion.resolucion import pendientes

# Las decisiones que hacen valer un monto en el caso; las otras dos cierran sin aportar.
DECISIONES_CON_HECHO = frozenset(
    {Decision.USAR_DIAN, Decision.USAR_DOCUMENTO, Decision.USAR_OTRO}
)

# Códigos de los avisos del ensamble (los lee T6 al fusionarlos en la liquidación).
PENSION_DISTRIBUIDA_UNIFORME = "PENSION_DISTRIBUIDA_UNIFORME"
DIVIDENDOS_SIN_DESAGREGAR = "DIVIDENDOS_SIN_DESAGREGAR"
RETENCION_SIN_INGRESO = "RETENCION_SIN_INGRESO"

# El motor no cubre la cédula del trabajador independiente: un hecho de estos conceptos
# no tiene modelo en el caso y silenciarlo haría desaparecer una cédula completa.
_SIN_MODELO = frozenset({Concepto.HONORARIOS, Concepto.SERVICIOS, Concepto.OTROS})

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
    ensamble = _Ensamble()
    grupos: dict[str, list[Partida]] = {}
    for p in partidas:
        if p.resolucion is None or p.resolucion.decision not in DECISIONES_CON_HECHO:
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
    for del_tercero in grupos.values():
        _ensamblar_tercero(ensamble, del_tercero)
    return ensamble


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

    sin_modelo = sorted(str(c) for c in por_concepto if c in _SIN_MODELO)
    if sin_modelo:
        raise NotImplementedError(
            f"El caso tributario todavía no modela estos conceptos: "
            f"{', '.join(sin_modelo)}. El motor no cubre la cédula de independientes; "
            "esos ingresos se declaran a mano mientras tanto."
        )

    # LA RETENCIÓN DEL TERCERO VIVE EN DOS SITIOS (herencia de T4, medida): la partida
    # RETENCION (filas que la DIAN asignó al renglón 132) y la retención que afirma la
    # versión escogida del ingreso (la casilla del 220). Son el mismo hecho contado dos
    # veces: rige UNA fuente y NUNCA se suman — sumarlas declara 16M donde hay 8M y el
    # saldo a favor queda inflado. Manda la partida explícita, que es el renglón que la
    # propia DIAN asignó (declarar más retención que lo reportado por el tercero casi
    # garantiza requerimiento); sin ella, la de la versión escogida.
    retenciones = por_concepto.pop(Concepto.RETENCION, [])
    retencion_pendiente = (
        sum(_resuelta(p).valor for p in retenciones) if retenciones else None
    )

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

    for concepto in _ORDEN_INGRESOS:
        for indice, p in enumerate(por_concepto.get(concepto, [])):
            retencion = _retencion_afirmada(p)
            extras = []
            if retencion_pendiente is not None:
                retencion = retencion_pendiente
                retencion_pendiente = None
                extras.append(f"retención de {', '.join(x.id for x in retenciones)}")
            if concepto is Concepto.SALARIOS:
                aportes = (salud, pension_obligatoria) if indice == 0 else ([], [])
                if any(aportes):
                    ids = [x.id for x in aportes[0] + aportes[1]]
                    extras.append(f"aportes de {', '.join(ids)}")
                ensamble.laborales.append(IngresoLaboral(
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
                ))
            elif concepto is Concepto.PENSIONES:
                pagador = p.nombre_tercero or p.nit_tercero
                total = _resuelta(p).valor
                ensamble.pensiones.append(IngresoPension(
                    pagador=pagador, mesadas=_mesadas(total),
                    retencion=retencion, fuente=_fuente(p, extras),
                ))
                ensamble.avisos.append(Flag(
                    codigo=PENSION_DISTRIBUIDA_UNIFORME,
                    mensaje=(
                        f"La pensión de {pagador} entró como el total anual "
                        f"({total:,} pesos) repartido en 12 mesadas iguales: correcto "
                        "si la mesada fue pareja, equivocado si hubo retroactivos o "
                        "reajustes — verificar contra los comprobantes del pagador."
                    ),
                ))
            elif concepto is Concepto.RENDIMIENTOS:
                ensamble.rendimientos.append(Rendimiento(
                    entidad=p.nombre_tercero or p.nit_tercero,
                    valor=_resuelta(p).valor,
                    retencion=retencion, fuente=_fuente(p, extras),
                ))
            elif concepto is Concepto.ARRENDAMIENTOS:
                ensamble.arriendos.append(Arriendo(
                    # La exógena identifica a quien PAGÓ el canon, no al inmueble; los
                    # costos (predial, administración...) tampoco salen del cruce y
                    # entran después por la captura.
                    inmueble=p.nombre_tercero or p.nit_tercero,
                    canon_total=_resuelta(p).valor,
                    retencion=retencion, fuente=_fuente(p, extras),
                ))
            elif concepto is Concepto.DIVIDENDOS:
                nombre = p.nombre_tercero or p.nit_tercero
                ensamble.dividendos.append(Dividendo(
                    sociedad_nit=p.nit_tercero,
                    sociedad_nombre=p.nombre_tercero,
                    # La partida trae UN número y el modelo exige el desglose. Se asume
                    # GRAVADOS — la dirección que nunca subdeclara, como el componente
                    # inflacionario en 0% — y el aviso deja la decisión a la vista.
                    gravados=_resuelta(p).valor,
                    no_gravados=0,
                    retencion=retencion, fuente=_fuente(p, extras),
                ))
                ensamble.avisos.append(Flag(
                    codigo=DIVIDENDOS_SIN_DESAGREGAR,
                    mensaje=(
                        f"Los dividendos de {nombre} entraron completos como gravados: "
                        "la partida no distingue la parte no gravada (art. 49) y "
                        "asumirla sin soporte bajaría el impuesto. Con el certificado "
                        "de la sociedad se desagregan y la carga puede bajar."
                    ),
                ))

    if retencion_pendiente is not None:
        # Declararla sola fabrica un saldo a favor sin ingreso que lo sostenga;
        # perderla en silencio regala plata del cliente. No entra, y queda a la vista.
        nombre = retenciones[0].nombre_tercero or retenciones[0].nit_tercero
        ensamble.avisos.append(Flag(
            codigo=RETENCION_SIN_INGRESO,
            mensaje=(
                f"La retención reportada por {nombre} ({retencion_pendiente:,} pesos) "
                "quedó resuelta sin ningún ingreso del mismo tercero en el caso: no se "
                "declaró, porque una retención sin ingreso fabrica un saldo a favor "
                "sin sustento. Revisar de qué ingreso viene."
            ),
        ))


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
    """La retención que afirma la versión escogida; 0 si no la afirma o no hay versión
    (con USAR_OTRO la vía para una retención real es resolver la partida RETENCION)."""
    version = _version_escogida(p)
    if version is None or version.retencion is None:
        return 0
    return version.retencion


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
    return fuente.model_copy(
        update={"celda": version.celda, "confianza": version.confianza}
    )
