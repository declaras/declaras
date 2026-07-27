"""Las resoluciones: cómo una partida cruzada se convierte en una cifra decidida.

Dos caminos ponen resolución. `resolver` es el del contador: valida que la decisión sea
posible para el estado de la partida y deja huella de quién y por qué. `autorresolver` es
el del sistema, con exactamente dos automatismos: cerrar las COINCIDE (los dos lados dicen
lo mismo) y ponerles una provisional USAR_DIAN a las SOLO_DIAN para que el 210 preliminar
exista sin esperar documentos. Todo lo demás — discrepancias, conceptos sin clasificar,
documentos sueltos y CUALQUIER partida ajena — es de una persona.

`refrescar` reconcilia lo resuelto con una re-derivación del cruce (documento nuevo,
re-consulta a la DIAN): las provisionales del sistema se reemplazan siempre; las del
contador sobreviven solo si su huella —las cifras que la persona vio al decidir— sigue
coincidiendo.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

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

# Qué decisión es posible sobre qué desenlace del cruce. La tabla del brief, con UNA
# desviación autorizada por la herencia de T4 (riesgo 2 de la ronda 2): SOLO_DOCUMENTO
# también admite CERRAR_SIN_SOPORTE. Sin ella, la partida suelta sin NIT que duplica un
# hecho ya conciliado (exógena sin NIT + 220 con NIT del mismo empleador) no tenía salida
# que NO aportara hecho: sus dos únicas decisiones metían la misma plata dos veces al caso.
_DECISIONES_POR_ESTADO: dict[EstadoPartida, frozenset[Decision]] = {
    EstadoPartida.COINCIDE: frozenset({Decision.USAR_DOCUMENTO, Decision.USAR_DIAN}),
    EstadoPartida.DISCREPANCIA: frozenset(
        {Decision.USAR_DOCUMENTO, Decision.USAR_DIAN, Decision.USAR_OTRO}
    ),
    EstadoPartida.SOLO_DIAN: frozenset(
        {Decision.USAR_DIAN, Decision.MARCAR_AJENO, Decision.USAR_OTRO}
    ),
    EstadoPartida.SOLO_DOCUMENTO: frozenset(
        {Decision.USAR_DOCUMENTO, Decision.USAR_OTRO, Decision.CERRAR_SIN_SOPORTE}
    ),
    # Sin concepto no se sabe a qué cédula del 210 iría el valor: no puede aportar hecho.
    EstadoPartida.CONCEPTO_DESCONOCIDO: frozenset(
        {Decision.MARCAR_AJENO, Decision.CERRAR_SIN_SOPORTE}
    ),
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
    return _con_resolucion(
        partida, decision, motivo=motivo, quien=quien, origen=Origen.CONTADOR,
        valor=valor, nota=nota,
    )


def autorresolver(partidas: list[Partida]) -> list[Partida]:
    """Los DOS automatismos del sistema. Pura: no muta la lista de entrada.

    Una partida ajena (`reportado_a is not None`) NUNCA se toca, en ningún estado — el
    guard va antes de mirar el estado, porque una provisional sobre una ajena liquidaría
    plata de otra persona en el 210 preliminar (herencia de T4). El precio: una exógena
    con filas ajenas no produce preliminar hasta que una persona las marque — que es
    exactamente lo que debe pasar, porque la alternativa (excluirlas solas) escondería
    plata que sí puede ser del titular ("misma cédula, otro nombre").
    """
    resueltas: list[Partida] = []
    for p in partidas:
        if p.resolucion is not None or p.reportado_a is not None:
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


def refrescar(nuevas: list[Partida], guardadas: list[Partida]) -> list[Partida]:
    """Reconcilia una re-derivación del cruce con las resoluciones que ya había.

    `nuevas` es la lista fresca (abrir + reincorporar todo); `guardadas` la persistida.
    Por id: una resolución de SISTEMA se reemplaza SIEMPRE (era provisional; el
    autorresolver del final vuelve a poner las que sigan aplicando); una de CONTADOR se
    preserva solo si su huella coincide con las cifras de la partida nueva — si no, la
    partida vuelve a pendiente con la nota de que los valores cambiaron (sumada a la nota
    fresca del cruce, no encima de ella). Una guardada cuyo id ya no existe en `nuevas`
    no transfiere su resolución a nada: la partida que la reemplace nace pendiente (los
    ids inestables están documentados en `_Grupo.id`; huérfana = a la cola otra vez).

    RESIDUO ASUMIDO de esa decisión de diseño: si el contador había MARCADO AJENA una
    partida sin marca estructural (p. ej. la `nombre:...` de un homónimo) y el id cambia,
    la partida nueva entra por los automatismos del final como cualquier otra — un
    SOLO_DIAN fresco recibe la provisional USAR_DIAN y esa plata vuelve al preliminar
    hasta que la persona la marque otra vez. No hay con qué ligar el id viejo al nuevo
    (ese es exactamente el problema documentado); las ajenas con marca estructural
    (`reportado_a`) NO caen acá porque la marca se re-deriva en el cruce y el guard de
    `autorresolver` las salta siempre.
    """
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
            resultado.append(nueva.model_copy(
                update={"nota": _con_nota(nueva.nota, NOTA_VALORES_CAMBIARON)}
            ))
    return autorresolver(resultado)


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
    }
    if not partida.nit_tercero:
        contenido["versiones"] = sorted(partida.versiones_documento)
    canonico = json.dumps(contenido, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode()).hexdigest()


def _cifras(version: Valor | None) -> list[int | None] | None:
    return None if version is None else [version.monto, version.retencion]
