"""Las resoluciones: cómo una partida cruzada se convierte en una cifra decidida.

Dos caminos ponen resolución. `resolver` es el del contador: valida que la decisión sea
posible para el estado de la partida y deja huella de quién y por qué. `autorresolver` es
el del sistema, con exactamente tres automatismos: cerrar las COINCIDE (los dos lados dicen
lo mismo), ponerles una provisional USAR_DIAN a las SOLO_DIAN para que el 210 preliminar
exista sin esperar documentos, y aceptar el documento en las SOLO_DOCUMENTO cuyo concepto la
exógena NO puede corroborar por reportarlo bajo otro NIT (los aportes obligatorios de un
220; ver `CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO`). Todo lo demás — discrepancias, conceptos
sin clasificar, documentos sueltos y CUALQUIER partida ajena — es de una persona.

`refrescar` reconcilia lo resuelto con una re-derivación del cruce (documento nuevo,
re-consulta a la DIAN): las provisionales del sistema se reemplazan siempre; las del
contador sobreviven solo si su huella —las cifras que la persona vio al decidir— sigue
coincidiendo.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from declaras.services.conciliacion.conceptos import CONCEPTOS_FUERA_DEL_MOTOR, Concepto
from declaras.services.conciliacion.cruce import _con_nota
from declaras.services.conciliacion.modelos import (
    Decision,
    EstadoPartida,
    Motivo,
    Origen,
    Partida,
    Resolucion,
    Valor,
)

# El `quien` de las resoluciones automáticas. Es un actor, no una persona: la interfaz de
# T6 lo distingue de un correo de contador.
QUIEN_SISTEMA = "sistema"

# La nota con que `refrescar` devuelve a pendiente una partida cuya huella ya no coincide.
# Texto del brief, literal: es lo que el contador lee en la cola.
NOTA_VALORES_CAMBIARON = "los valores cambiaron desde la resolución anterior"

# Los conceptos del TERCER automatismo (ruling del coordinador, ronda de fixes 1 de T6).
#
# LA RAZÓN ES UNA ASIMETRÍA DE NITs, no una comodidad: la exógena reporta los aportes
# obligatorios bajo el NIT de la EPS o del fondo de pensiones, nunca bajo el del empleador,
# así que la partida de aportes que abre un 220 NUNCA va a cruzar contra una fila del
# reporte — es imposible por construcción, no "todavía no llegó". Pedirle al contador una
# decisión ahí no gana información: no hay dos versiones que comparar, y el 220 es el
# soporte que la ley exige para esa deducción. Sin esto, cada 220 dejaba TRES renglones por
# decidir (la discrepancia de salarios más estos dos), y dos de ellos no tenían nada que
# decidir.
#
# ES PROVISIONAL, en el mismo sentido que los otros dos automatismos: la resolución queda
# con origen SISTEMA, visible en la lista, y cualquier cambio de cifras la invalida por
# huella y la devuelve a la cola.
#
# LO QUE ESTE AUTOMATISMO NO ES: un "aceptar todo lo del 220". La discrepancia de salarios
# sigue siendo del contador — ahí SÍ hay dos versiones que comparar y la decisión necesita
# criterio. Un agente futuro que quiera ampliar esto a otros conceptos tiene que mostrar la
# misma imposibilidad estructural de cruce; hoy, medido contra `TIPO_A_CLAVE`, el único
# documento que abre partidas de estos dos conceptos es el 220 del empleador.
#
# POR QUÉ LA GUARDA ES CONCEPTO + ESTADO Y NO `doc_type`: la afirmación "no hay contraparte
# en la DIAN" se AUTOVERIFICA con `SOLO_DOCUMENTO`. Si algún día la exógena reportara los
# aportes bajo el NIT del empleador, la partida nacería COINCIDE o DISCREPANCIA y el
# automatismo NO dispararía — el motivo no puede volverse mentira por este camino (y
# `_ESTADOS_POR_MOTIVO` lo exige además al resolver a mano).
#
# CONSECUENCIA PARA LAS TAREAS QUE SIGUEN, ADVERTIDA: cualquier tipo de documento nuevo que
# se registre en `TIPO_A_CLAVE` abriendo APORTES_SALUD o APORTES_PENSION queda cubierto por
# este automatismo EN SILENCIO, sin pasar por acá. Quien agregue ese tipo tiene que verificar
# que su documento también sea la fuente autoritativa del aporte; si no lo es, hay que
# discriminar por `doc_type` (y eso exige que la partida lo lleve, que hoy no lo lleva).
CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO = frozenset(
    {Concepto.APORTES_SALUD, Concepto.APORTES_PENSION}
)

# Qué decisión es posible sobre qué desenlace del cruce. La tabla del brief, con DOS
# desviaciones autorizadas. (1) Herencia de T4 (riesgo 2 de la ronda 2): SOLO_DOCUMENTO
# también admite CERRAR_SIN_SOPORTE — sin ella, la partida suelta sin NIT que duplica un
# hecho ya conciliado no tenía salida que NO aportara hecho: sus dos únicas decisiones
# metían la misma plata dos veces al caso. (2) Ruling de la ronda de fixes 1 de T5:
# LLEVAR_A_MANO sobre todos los estados con concepto — una partida cuyo concepto el motor
# no liquida (CONCEPTOS_FUERA_DEL_MOTOR) necesita una salida sin hecho para que el
# contador la sume a mano; con la tabla literal, sus únicas salidas aportaban hecho y
# a_caso tronaba el caso COMPLETO. El gate por concepto vive en `resolver` (la tabla es
# por estado; esta decisión es la única condicionada también por el concepto).
_DECISIONES_POR_ESTADO: dict[EstadoPartida, frozenset[Decision]] = {
    EstadoPartida.COINCIDE: frozenset(
        {Decision.USAR_DOCUMENTO, Decision.USAR_DIAN, Decision.LLEVAR_A_MANO}
    ),
    EstadoPartida.DISCREPANCIA: frozenset(
        {Decision.USAR_DOCUMENTO, Decision.USAR_DIAN, Decision.USAR_OTRO,
         Decision.LLEVAR_A_MANO}
    ),
    EstadoPartida.SOLO_DIAN: frozenset(
        {Decision.USAR_DIAN, Decision.MARCAR_AJENO, Decision.USAR_OTRO,
         Decision.LLEVAR_A_MANO}
    ),
    EstadoPartida.SOLO_DOCUMENTO: frozenset(
        {Decision.USAR_DOCUMENTO, Decision.USAR_OTRO, Decision.CERRAR_SIN_SOPORTE,
         Decision.LLEVAR_A_MANO}
    ),
    # Sin concepto no se sabe a qué cédula del 210 iría el valor: no puede aportar hecho
    # (y tampoco "llevarse a mano": no se sabe QUÉ se estaría llevando).
    EstadoPartida.CONCEPTO_DESCONOCIDO: frozenset(
        {Decision.MARCAR_AJENO, Decision.CERRAR_SIN_SOPORTE}
    ),
}

# Qué motivo puede acompañar a qué decisión (M1 de la ronda 2): la pareja es la huella
# que lee un auditor — "USAR_DIAN (NO_ES_MIO)" en la Fuente de un hecho declarado es un
# contrasentido que nadie puede interpretar después. DECISION_DEL_CONTADOR es el motivo
# genérico de toda decisión humana; LLEVAR_A_MANO exige el suyo (el ruling de la ronda 1
# pidió que el nombre Y el motivo digan "el soporte existe, falta el motor").
_MOTIVOS_POR_DECISION: dict[Decision, frozenset[Motivo]] = {
    Decision.USAR_DIAN: frozenset(
        {Motivo.ERROR_DEL_CERTIFICADO, Motivo.FALTA_DOCUMENTO, Motivo.COINCIDEN,
         Motivo.DECISION_DEL_CONTADOR}
    ),
    Decision.USAR_DOCUMENTO: frozenset(
        {Motivo.ERROR_DEL_TERCERO, Motivo.COINCIDEN, Motivo.DECISION_DEL_CONTADOR,
         Motivo.SIN_CONTRAPARTE_DIAN}
    ),
    Decision.USAR_OTRO: frozenset({Motivo.DECISION_DEL_CONTADOR}),
    Decision.MARCAR_AJENO: frozenset({Motivo.NO_ES_MIO, Motivo.DECISION_DEL_CONTADOR}),
    Decision.CERRAR_SIN_SOPORTE: frozenset(
        {Motivo.FALTA_DOCUMENTO, Motivo.NO_ES_MIO, Motivo.DECISION_DEL_CONTADOR}
    ),
    Decision.LLEVAR_A_MANO: frozenset({Motivo.FUERA_DEL_MOTOR}),
}


# Motivos que AFIRMAN algo sobre el otro lado de la partida, y por tanto solo son ciertos
# en ciertos estados. La validación de `_MOTIVOS_POR_DECISION` es decisión×motivo, así que
# sin esto se aceptaba (y `decisiones_posibles` OFRECÍA) "usé el documento porque la DIAN no
# reporta nada" sobre un renglón donde la DIAN reporta 87.400.000 — un contrasentido en la
# `Fuente` que lee un auditor, la misma clase que cerró el M1 de la ronda 2 de T5.
#
# Solo se acotan los DOS motivos que hacen una afirmación verificable. `ERROR_DEL_TERCERO` y
# `ERROR_DEL_CERTIFICADO` quedan libres a propósito: "el tercero reportó mal" es lo que
# explica que la DIAN no tenga el hecho, así que son legítimos sobre una partida de un solo
# lado, y acotarlos rompería el uso idiomático (los aportes de un 220 se aceptan así).
_ESTADOS_POR_MOTIVO: dict[Motivo, frozenset[EstadoPartida]] = {
    # "Coinciden" solo es cierto si los dos lados existen y cerraron.
    Motivo.COINCIDEN: frozenset({EstadoPartida.COINCIDE}),
    # "No hay contraparte en la DIAN" se autoverifica con el estado: si la hubiera, la
    # partida sería COINCIDE o DISCREPANCIA.
    Motivo.SIN_CONTRAPARTE_DIAN: frozenset({EstadoPartida.SOLO_DOCUMENTO}),
}


def resolver(
    partida: Partida,
    decision: Decision,
    *,
    motivo: Motivo,
    quien: str,
    valor: int | None = None,
    nota: str | None = None,
) -> Partida:
    """La decisión de una persona sobre una partida. Pura: devuelve una copia resuelta.

    Volver a resolver una partida ya resuelta reemplaza la resolución (el contador puede
    corregirse); el estado del cruce no cambia — cuenta qué desenlace tuvo el cruce, no
    qué se decidió sobre él.
    """
    permitidas = _DECISIONES_POR_ESTADO[partida.estado]
    if decision not in permitidas:
        posibles = ", ".join(sorted(permitidas))
        raise ValueError(
            f"La decisión {decision} no aplica a una partida en estado {partida.estado}; "
            f"las posibles son: {posibles}."
        )
    if decision is Decision.LLEVAR_A_MANO and partida.concepto not in CONCEPTOS_FUERA_DEL_MOTOR:
        # LLEVAR_A_MANO existe porque al motor le falta el concepto (ruling de la ronda
        # de fixes 1), NO para sacar de la liquidación un ingreso que sí se liquida:
        # eso sería subdeclarar con un gate más débil que el resto de la tabla.
        raise ValueError(
            f"La decisión LLEVAR_A_MANO es solo para conceptos que el motor no liquida "
            f"todavía; {partida.concepto} sí tiene modelo en el caso y excluirlo lo "
            "subdeclararía."
        )
    return _con_resolucion(
        partida, decision, motivo=motivo, quien=quien, origen=Origen.CONTADOR,
        valor=valor, nota=nota,
    )


def autorresolver(partidas: list[Partida]) -> list[Partida]:
    """Los TRES automatismos del sistema. Pura: no muta la lista de entrada.

    Una partida ajena (`reportado_a is not None`) NUNCA se toca, en ningún estado — el
    guard va antes de mirar el estado, porque una provisional sobre una ajena liquidaría
    plata de otra persona en el 210 preliminar (herencia de T4). El precio: una exógena
    con filas ajenas no produce preliminar hasta que una persona las marque — que es
    exactamente lo que debe pasar, porque la alternativa (excluirlas solas) escondería
    plata que sí puede ser del titular ("misma cédula, otro nombre").

    Un concepto FUERA del alcance del motor tampoco se toca (ruling de la ronda de
    fixes 1): la provisional sobre honorarios garantizaba que `a_caso` tronara con el
    NotImplementedError Y escondía la partida de la cola (resuelta = no pendiente) — el
    contador no tenía dónde verla ni cómo sacarla. Queda pendiente, visible, y la salida
    es suya: LLEVAR_A_MANO (o USAR_OTRO/USAR_DIAN si insiste, que revientan honesto).

    Una provisional RANCIA se descarta antes de decidir (C1 de la ronda de fixes 2):
    por el camino incremental (`incorporar` de a un documento) la partida cambia de
    cifras y de estado con la resolución ARRASTRADA en el `model_copy` — la provisional
    USAR_DIAN de 87.4M seguía pegada a una DISCREPANCIA contra un 220 de 85M, `a_caso`
    declaraba la cifra vieja y la discrepancia jamás llegaba a la cola (cero pendientes,
    cero avisos). La detección es la que ya existe para el contador: la huella. La
    vigente (huella intacta) no se toca — descartarla y recrearla cambiaría `cuando`
    sin que nada haya cambiado. Una decisión de CONTADOR nunca se descarta acá: eso es
    de `refrescar`, que la invalida CON la nota de valores cambiados.
    """
    resueltas: list[Partida] = []
    for p in partidas:
        if (p.resolucion is not None
                and p.resolucion.origen is Origen.SISTEMA
                and p.resolucion.huella != _huella(p)):
            p = p.model_copy(update={"resolucion": None})
        if (p.resolucion is not None or p.reportado_a is not None
                or p.concepto in CONCEPTOS_FUERA_DEL_MOTOR):
            resueltas.append(p)
        elif p.estado is EstadoPartida.COINCIDE:
            resueltas.append(_con_resolucion(
                p, Decision.USAR_DOCUMENTO, motivo=Motivo.COINCIDEN,
                quien=QUIEN_SISTEMA, origen=Origen.SISTEMA,
            ))
        elif p.estado is EstadoPartida.SOLO_DIAN:
            resueltas.append(_con_resolucion(
                p, Decision.USAR_DIAN, motivo=Motivo.FALTA_DOCUMENTO,
                quien=QUIEN_SISTEMA, origen=Origen.SISTEMA,
            ))
        elif (p.estado is EstadoPartida.SOLO_DOCUMENTO
                and p.concepto in CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO):
            # El TERCER automatismo (ver `CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO`): la exógena
            # reporta los aportes obligatorios bajo el NIT de la EPS o del fondo, así que
            # esta partida no puede cruzar contra ninguna fila del reporte —imposible por
            # construcción, no "todavía no llegó"— y el 220 es el soporte que la ley exige.
            # Provisional como las otras dos: origen SISTEMA, visible, y un cambio de cifras
            # la invalida por huella.
            resueltas.append(_con_resolucion(
                p, Decision.USAR_DOCUMENTO, motivo=Motivo.SIN_CONTRAPARTE_DIAN,
                quien=QUIEN_SISTEMA, origen=Origen.SISTEMA,
            ))
        else:
            resueltas.append(p)
    return resueltas


def pendientes(partidas: list[Partida]) -> list[Partida]:
    """Las partidas que esperan a una persona, con la plata en juego primero."""
    return sorted(
        (p for p in partidas if p.resolucion is None),
        key=_plata_en_juego,
        reverse=True,
    )


def refrescar(
    nuevas: list[Partida], guardadas: list[Partida]
) -> tuple[list[Partida], list[Partida]]:
    """Reconcilia una re-derivación del cruce con las resoluciones que ya había.

    Devuelve `(partidas, huerfanas)`. `nuevas` es la lista fresca (abrir + reincorporar
    todo); `guardadas` la persistida. Por id: una resolución de SISTEMA se reemplaza
    SIEMPRE (era provisional; el autorresolver del final vuelve a poner las que sigan
    aplicando); una de CONTADOR se preserva solo si su huella coincide con las cifras de
    la partida nueva — si no, la partida vuelve a pendiente con la nota de que los
    valores cambiaron (sumada a la nota fresca del cruce, no encima de ella). Una
    guardada cuyo id ya no existe en `nuevas` no transfiere su resolución a nada: la
    partida que la reemplace nace pendiente (los ids inestables están documentados en
    `_Grupo.id`; huérfana = a la cola otra vez).

    Las `huerfanas` son esas guardadas cuyo id desapareció, tal cual estaban —
    resoluciones incluidas (I5 de la ronda 2): botarlas en silencio escondía deducción
    real (los aportes de un 220 que la re-consulta no trae) y decisiones de una persona.
    Quien llama decide qué hacer con ellas (mostrarlas, re-anclarlas, descartarlas), pero
    enterarse no es opcional: por eso van en el retorno y no en una función aparte.

    RESIDUO ASUMIDO de esa decisión de diseño: si el contador había MARCADO AJENA una
    partida sin marca estructural (p. ej. la `nombre:...` de un homónimo) y el id cambia,
    la partida nueva entra por los automatismos del final como cualquier otra — un
    SOLO_DIAN fresco recibe la provisional USAR_DIAN y esa plata vuelve al preliminar
    hasta que la persona la marque otra vez. No hay con qué ligar el id viejo al nuevo
    (ese es exactamente el problema documentado); las ajenas con marca estructural
    (`reportado_a`) NO caen acá porque la marca se re-deriva en el cruce y el guard de
    `autorresolver` las salta siempre.
    """
    ids_nuevos = {p.id for p in nuevas}
    huerfanas = [p for p in guardadas if p.id not in ids_nuevos]
    previas = {p.id: p for p in guardadas}
    resultado: list[Partida] = []
    for nueva in nuevas:
        previa = previas.get(nueva.id)
        anterior = previa.resolucion if previa is not None else None
        if anterior is None or anterior.origen is Origen.SISTEMA:
            resultado.append(nueva)
        elif anterior.huella == _huella(nueva):
            resultado.append(nueva.model_copy(update={"resolucion": anterior}))
        else:
            # `resolucion: None` explícito (C1 de la ronda de fixes 2): `nuevas` no
            # siempre llega fresca — la lista incremental trae la decisión del contador
            # ARRASTRADA por `incorporar` sobre cifras nuevas, y sin esto la rama ponía
            # la nota pero dejaba la resolución rancia pegada (resuelta con el valor
            # viejo y fuera de la cola).
            resultado.append(nueva.model_copy(update={
                "resolucion": None,
                "nota": _con_nota(nueva.nota, NOTA_VALORES_CAMBIARON),
            }))
    return autorresolver(resultado), huerfanas


def _con_resolucion(
    partida: Partida,
    decision: Decision,
    *,
    motivo: Motivo,
    quien: str,
    origen: Origen,
    valor: int | None = None,
    nota: str | None = None,
) -> Partida:
    if motivo not in _MOTIVOS_POR_DECISION[decision]:
        posibles = ", ".join(sorted(_MOTIVOS_POR_DECISION[decision]))
        raise ValueError(
            f"El motivo {motivo} no corresponde a la decisión {decision}; "
            f"los posibles son: {posibles}."
        )
    estados = _ESTADOS_POR_MOTIVO.get(motivo)
    if estados is not None and partida.estado not in estados:
        raise ValueError(
            f"El motivo {motivo} afirma algo que no es cierto de una partida en estado "
            f"{partida.estado}; solo aplica a: {', '.join(sorted(estados))}."
        )
    resolucion = Resolucion(
        decision=decision,
        valor=_derivar_valor(partida, decision, valor),
        motivo=motivo,
        origen=origen,
        huella=_huella(partida),
        nota=nota,
        quien=quien,
        cuando=datetime.now(tz=UTC),
    )
    return partida.model_copy(update={"resolucion": resolucion})


def _derivar_valor(partida: Partida, decision: Decision, valor: int | None) -> int:
    """El monto que la decisión hace valer. Solo USAR_OTRO lo trae explícito."""
    if decision is Decision.USAR_OTRO:
        if valor is None:
            raise ValueError("La decisión USAR_OTRO exige el valor que va a regir.")
        if valor < 0:
            raise ValueError("El valor de una resolución va en pesos; no puede ser negativo.")
        return valor
    if valor is not None:
        raise ValueError(
            "Solo la decisión USAR_OTRO acepta un valor explícito; "
            "las demás lo toman de la versión que escogen."
        )
    if decision is Decision.USAR_DIAN:
        if partida.version_dian is None:
            # Defensa: la tabla ya lo impide para los estados que el cruce produce, pero
            # una partida construida a mano puede llegar incoherente (sin validadores).
            raise ValueError("La partida no tiene versión de la DIAN de dónde tomar el valor.")
        return partida.version_dian.monto
    if decision is Decision.USAR_DOCUMENTO:
        if partida.version_documento is None:
            raise ValueError("La partida no tiene versión del documento de dónde tomar el valor.")
        return partida.version_documento.monto
    # MARCAR_AJENO / CERRAR_SIN_SOPORTE: no aportan hecho, no hacen valer ningún monto.
    return 0


def _plata_en_juego(partida: Partida) -> int:
    """Cuánto dinero depende de que alguien mire esta partida.

    Con las dos versiones es la mayor de las dos diferencias (una discrepancia solo de
    retención también es plata: declarar retención de más casi garantiza requerimiento).
    Con una sola versión TODO el monto está en juego — la diferencia daría 0 y una suelta
    de 85 millones quedaría debajo de una discrepancia de 100 pesos. La ajena cae acá
    (sus diferencias van forzadas a 0): pesa por el monto que hay que confirmar.
    """
    if partida.version_dian is not None and partida.version_documento is not None:
        if partida.reportado_a is not None:
            # Incoherente por construcción (el cruce no adjunta documentos a una ajena),
            # pero construible a mano: pesa por lo que la DIAN dice, como toda ajena.
            return partida.version_dian.monto
        return max(partida.diferencia_monto, partida.diferencia_retencion)
    unica = partida.version_dian or partida.version_documento
    return unica.monto if unica is not None else 0


def _huella(partida: Partida) -> str:
    """El hash de las cifras que el resolvedor vio al decidir (digest completo, como
    `content_sha256`; el corto se deriva con `[:12]` si alguna interfaz lo necesita).

    Cubre monto y retención de las DOS versiones — los mismos números que el cruce compara
    (`_cifras_difieren`): una republicación de la DIAN que solo mueve la fila de celda no
    invalida la decisión del contador, porque los valores no cambiaron y la nota diría
    mentiras. En el camino sin NIT cubre ADEMÁS la membresía de `versiones_documento`: ahí
    una versión nueva con cifras iguales sí es información nueva ("¿mismo certificado
    repetido o dos terceros?", ruling de F1) y la resolución tiene que volver a la persona;
    con NIT, mismas cifras = mismo certificado (F4) y re-decidir sería trabajo inventado.
    """
    contenido: dict[str, object] = {
        "dian": _cifras(partida.version_dian),
        "documento": _cifras(partida.version_documento),
        # A quién pertenece la plata es parte de lo que el resolvedor vio (I2 de la
        # ronda 2, el gemelo del M4): una partida que se volvió ajena conservando id y
        # cifras invalida la decisión — que era sobre plata del titular. En el cruce
        # real el sufijo `:reportado-a:` cambia el id y la resolución queda huérfana
        # por esa vía; esto cierra el camino construible a mano.
        "reportado_a": partida.reportado_a,
    }
    if not partida.nit_tercero:
        contenido["versiones"] = sorted(partida.versiones_documento)
    canonico = json.dumps(contenido, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()


def _cifras(version: Valor | None) -> list[int | None] | None:
    return None if version is None else [version.monto, version.retencion]
