"""El servicio que cose las dos mitades: la ingesta de documentos y el cálculo del 210.

LA REGLA QUE ORDENA TODO ESTE MÓDULO: nunca se llama `incorporar` sobre partidas ya
resueltas. Cada vez que algo cambia (llegó un documento, la DIAN republicó el reporte) se
RECONSTRUYE desde cero —`abrir` la exógena vigente, reincorporar todos los documentos que
el conciliador sabe cruzar— y el resultado pasa por `refrescar(nuevas, guardadas)`. La
razón está medida: por el camino corto una resolución provisional del sistema queda pegada
a cifras que ya cambiaron, el 210 declara la cifra vieja y la discrepancia jamás llega a la
cola del contador. El producto existe para detectar exactamente eso.

`refrescar` devuelve DOS cosas y las dos se usan: las partidas y las HUÉRFANAS —
resoluciones cuya partida desapareció de la re-derivación—. Se persisten y se listan como
"resoluciones sin partida": descartarlas al desempacar botaría en silencio la decisión de
una persona y la deducción que la sostenía.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from declaras.caso import Beneficios, CasoTributario, Contribuyente, Movimientos
from declaras.documents.models import DocumentReading
from declaras.domain.case import Case, CaseDetail, CaseDocument, CaseStatus, FlagSeverity
from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import (
    AnioSinParametrosError,
    CaseNotFoundError,
    ConflictoDeConcurrenciaError,
    DecisionNoAplicaError,
    LiquidacionBloqueadaError,
    LiquidacionNoDisponibleError,
    PartidaNoEncontradaError,
    PeticionNoEncontradaError,
    SinReporteDeTercerosError,
)
from declaras.domain.models import DocumentType
from declaras.motor import Flag, Liquidacion
from declaras.observability import get_logger
from declaras.parametros import ParametrosAnio, cargar
from declaras.render import Casilla, borrador_html, formulario_210, memoria_markdown
from declaras.services.comparacion_210 import Comparacion210, Contra, comparar
from declaras.services.conciliacion import (
    CONCEPTOS_FUERA_DEL_MOTOR,
    TIPO_A_CLAVE,
    ClaseDeIngreso,
    Concepto,
    Decision,
    LiquidacionVersionada,
    Motivo,
    Origen,
    Partida,
    Peticion,
    Respuesta,
    a_caso,
    abrir,
    autorresolver,
    bloqueantes,
    derivar_peticiones,
    etiqueta_de_pregunta,
    ganancia,
    incorporar,
    liquidar_conciliado,
    liquidar_y_versionar,
    movimientos_de,
    pendientes,
    refrescar,
    resolver,
)

# El criterio de "cuánta plata depende de que alguien mire esta partida" se importa del
# conciliador a propósito, aunque sea privado: es el mismo con que se ordena la cola de
# pendientes, y una segunda copia haría que la cola del contador y la lista del API se
# ordenaran distinto sin que nadie lo note.
from declaras.services.conciliacion.beneficios import beneficios_de
from declaras.services.conciliacion.recomendaciones import (
    Recomendaciones,
    derivar_recomendaciones,
)
from declaras.services.conciliacion.resolucion import _plata_en_juego

log = get_logger(__name__)

# El tipo con que el reporte de terceros vive en el expediente.
DOC_TYPE_EXOGENA = "EXOGENA"

# Qué le pasó a un archivo que acabó de llegar, desde el punto de vista del cruce.
ESTADO_EMPAREJADO = "emparejado"
ESTADO_SIN_EMPAREJAR = "sin_emparejar"
ESTADO_A_BANDEJA = "a_bandeja"
# Cuarto desenlace, más allá de los tres del contrato del plan: el archivo NO entró. Decir
# "a bandeja" de algo que no se guardó afirmaría que quedó en la declaración esperando
# revisión manual, y el contador lo buscaría donde no está. Sin este estado la única salida
# era abortar la request con 500 dejando persistidos los archivos anteriores (F7).
ESTADO_NO_RECIBIDO = "no_recibido"

_MOTIVO_A_BANDEJA = (
    "El conciliador todavía no sabe cruzar documentos de este tipo: el archivo queda en "
    "la declaración para revisarlo a mano."
)
_MOTIVO_SIN_LECTURA = (
    "El archivo no se pudo leer, así que no hay cifras que cruzar. Queda en la "
    "declaración y su alerta dice por qué."
)
_MOTIVO_SIN_EMPAREJAR = (
    "La DIAN no reporta este hecho: el documento abrió su propio renglón y hay que "
    "decidir qué se hace con él."
)
# Lo que falta cuando los renglones no salieron de los documentos que hay en el expediente:
# nunca se concilió, o entró un documento por un camino que no corre el cruce.
_FALTA_CONCILIAR = (
    "Los renglones no corresponden a los documentos que hay en esta declaración: entró "
    "algo nuevo (o todavía no se ha cruzado nada). Hay que conciliar antes de calcular."
)

# El archivo entró pero el cruce no alcanzó a correr (otra request estaba cambiando los
# renglones). NO se le pide al cliente que lo reenvíe: ya está guardado.
MOTIVO_CRUCE_NO_CORRIO = (
    "El archivo quedó guardado, pero el cruce no alcanzó a correr porque alguien más estaba "
    "cambiando la declaración. Hay que conciliar; el archivo no se vuelve a mandar."
)

_MOTIVO_SIN_CONCILIAR = (
    "Esta declaración todavía no se ha cruzado con el reporte de terceros: el archivo "
    "queda guardado y entra al cruce en cuanto se concilie."
)


@runtime_checkable
class ConciliacionRepository(Protocol):
    """Puerto de persistencia del conciliador. Lo implementa el adaptador de SQLAlchemy."""

    async def guardadas(self, case_id: UUID) -> list[Partida]: ...

    async def vivas(self, case_id: UUID) -> list[Partida]: ...

    async def huerfanas(self, case_id: UUID) -> list[Partida]: ...

    async def revision(self, case_id: UUID) -> int: ...

    async def huella(self, case_id: UUID) -> str | None: ...

    async def reemplazar_partidas(
        self,
        case_id: UUID,
        *,
        partidas: list[Partida],
        huerfanas: list[Partida],
        revision_esperada: int,
        huella_documentos: str,
    ) -> int: ...

    async def respuestas(self, case_id: UUID) -> list[Respuesta]: ...

    async def registrar_respuesta(self, case_id: UUID, respuesta: Respuesta) -> Respuesta: ...

    async def versiones(self, case_id: UUID) -> list[LiquidacionVersionada]: ...

    async def agregar_version(
        self, case_id: UUID, version: LiquidacionVersionada
    ) -> LiquidacionVersionada: ...


@dataclass
class Estado:
    """Lo que se sabe del cruce de una declaración en un momento dado."""

    partidas: list[Partida]
    huerfanas: list[Partida]
    # None cuando el caso todavía no se puede armar; `falta` dice por qué, con palabras
    # que el contador pueda accionar.
    caso: CasoTributario | None
    falta: str | None
    # La revisión del bloque de partidas que se leyó. Es la precondición con que se escribe:
    # si alguien más lo cambió en el intervalo, el reemplazo se niega en vez de pisarlo.
    revision: int = 0
    # ¿Los renglones persistidos salieron de los documentos que HAY en el expediente? Es
    # False cuando alguien metió (o reemplazó) un documento por un camino que no corre el
    # cruce, y cuando nunca se ha conciliado. Con esto en False el caso NO se arma: los
    # cuatro caminos que dependen de `caso` —borrador, memoria, cerrar y la vigencia de la
    # liquidación— se niegan del mismo guard, en vez de cada uno por su cuenta.
    corresponde_al_expediente: bool = True
    # Los avisos del camino de BENEFICIOS, que no pasa por partidas: la DIAN no reporta una
    # medicina prepagada, así que no hay nada que cruzar y sus alertas no pueden salir del
    # ensamble. Viajan en el estado porque se calculan donde se calcula el caso —el mismo
    # sitio único— y de acá los toma quien liquida.
    avisos_beneficios: list[Flag] = field(default_factory=list)

    @property
    def pendientes(self) -> list[Partida]:
        return pendientes(self.partidas)

    @property
    def por_estado(self) -> dict[str, int]:
        conteo: dict[str, int] = {}
        for partida in self.partidas:
            conteo[partida.estado.value] = conteo.get(partida.estado.value, 0) + 1
        return conteo

    @property
    def ordenadas(self) -> list[Partida]:
        """Por plata en juego, la más pesada primero: es el orden en que se trabajan."""
        return sorted(self.partidas, key=_plata_en_juego, reverse=True)


@dataclass
class Subido:
    """UN archivo que acabó de entrar al expediente, con la identidad con que quedó.

    `sha` es el identificador corto del documento guardado —la MISMA llave con que el cruce
    registra sus versiones— y es None cuando el archivo no se pudo guardar. La identidad va
    por acá y no por el nombre porque dos archivos homónimos en la misma request son dos
    documentos distintos, y con el nombre como llave los dos recibían el desenlace del
    último (F5).
    """

    archivo: str
    doc_type: str
    peticion_id: str | None
    sha: str | None
    motivo: str | None = None


@dataclass
class ArchivoIncorporado:
    """El desenlace de UN archivo de una subida masiva."""

    archivo: str
    doc_type: str
    estado: str
    peticion_cerrada: bool | None = None
    motivo: str | None = None


@dataclass
class Liquidaciones:
    preliminar: LiquidacionVersionada
    actual: LiquidacionVersionada
    # ¿La versión `actual` corresponde a los renglones que hay HOY? Es False cuando el
    # estado dejó de poder liquidarse desde que se guardó (llegó un documento y hay
    # decisiones pendientes): ahí `actual` es lo último que se pudo calcular, NO la
    # declaración de hoy, y la ganancia se mide contra ella. Sin esta marca el front pinta
    # la cifra de antes del último documento como si fuera la vigente.
    vigente: bool
    # Qué falta para que vuelva a haber una liquidación de hoy (None si `vigente`).
    falta: str | None

    @property
    def ganancia(self) -> int:
        return ganancia(self.preliminar, self.actual)


class ConciliacionService:
    def __init__(self, *, cases: CaseRepository, conciliacion: ConciliacionRepository) -> None:
        self._cases = cases
        self._repo = conciliacion

    # ─────────────────────────── casos de uso ───────────────────────────

    async def conciliar(self, case_id: UUID) -> Estado:
        """Reconstruye el cruce desde la exógena vigente y recalcula la liquidación.

        Es idempotente por construcción: reconstruir + `refrescar` reemplaza siempre las
        provisionales del sistema y preserva las decisiones del contador cuya huella siga
        coincidiendo. Volver a llamarla no duplica nada porque no acumula: reemplaza.
        """
        estado = await self._reconstruir(case_id)
        await self._asegurar_preliminar(case_id)
        await self._recalcular(case_id, estado)
        log.info(
            "conciliacion.conciliada",
            case_id=str(case_id),
            partidas=len(estado.partidas),
            pendientes=len(estado.pendientes),
            huerfanas=len(estado.huerfanas),
        )
        return estado

    async def estado(self, case_id: UUID) -> Estado:
        """Lo que ya está persistido, sin re-derivar nada."""
        detail = await self._detalle(case_id)
        revision = await self._repo.revision(case_id)
        partidas = await self._repo.vivas(case_id)
        huerfanas = await self._repo.huerfanas(case_id)
        return self._construir_estado(
            partidas=partidas,
            huerfanas=huerfanas,
            detail=detail,
            revision=revision,
            corresponde=await self._corresponde(case_id, detail),
        )

    async def _corresponde(self, case_id: UUID, detail: CaseDetail) -> bool:
        """¿Los renglones guardados salieron de los documentos que HAY en el expediente?

        `huella is None` = nunca se concilió. Se niega igual que si los documentos hubieran
        cambiado: un caso de cero renglones es liquidable para `a_caso` (cero hechos es un
        caso válido), así que sin esa mitad el guard falla ABIERTO y se cierra un 210 en cero
        teniendo documentos en la mano.
        """
        guardada = await self._repo.huella(case_id)
        return guardada is not None and guardada == self._huella_documentos(detail)

    def _construir_estado(
        self,
        *,
        partidas: list[Partida],
        huerfanas: list[Partida],
        detail: CaseDetail,
        revision: int,
        corresponde: bool,
    ) -> Estado:
        """EL ÚNICO SITIO donde se decide si hay caso que liquidar.

        Todo camino que produzca un `Estado` pasa por acá. No es ceremonia: cuando
        `resolver_partida` se armaba su propio `Estado` sin el veredicto de la huella se
        volvió un QUINTO camino que contradecía a los otros cuatro — respondía "no falta
        nada" mientras `GET /conciliacion`, en la request siguiente y sobre el mismo caso,
        decía que los renglones no corresponden, y de paso persistía una versión de la
        liquidación derivada de renglones viejos. Mientras la decisión viva en un solo sitio
        eso no puede volver a pasar.
        """
        if not corresponde:
            return Estado(
                partidas=partidas,
                huerfanas=huerfanas,
                caso=None,
                falta=_FALTA_CONCILIAR,
                revision=revision,
                corresponde_al_expediente=False,
            )
        # Los beneficios se arman donde se arma el caso: es el mismo sitio único, así que no
        # hay un camino que produzca un `Estado` con el caso de un lado y sus avisos del otro.
        beneficios, avisos_beneficios = beneficios_de(
            d.reading for d in detail.documents if d.reading is not None
        )
        caso, falta = self._intentar_caso(partidas, detail, beneficios)
        return Estado(
            partidas=partidas,
            huerfanas=huerfanas,
            caso=caso,
            falta=falta,
            revision=revision,
            avisos_beneficios=avisos_beneficios,
        )

    async def resolver_partida(
        self,
        case_id: UUID,
        partida_id: str,
        *,
        decision: Decision,
        motivo: Motivo,
        quien: str,
        valor: int | None = None,
        clase: ClaseDeIngreso | None = None,
        nota: str | None = None,
    ) -> tuple[Partida, Estado]:
        """Registra la decisión del contador sobre un renglón y recalcula."""
        estado = await self.estado(case_id)
        objetivo = next((p for p in estado.partidas if p.id == partida_id), None)
        if objetivo is None:
            raise PartidaNoEncontradaError(partida_id=partida_id, case_id=str(case_id))
        try:
            resuelta = resolver(
                objetivo,
                decision,
                motivo=motivo,
                quien=quien,
                valor=valor,
                clase=clase,
                nota=nota,
            )
        except ValueError as exc:
            # El conciliador valida decisión × estado × concepto y sus mensajes ya están
            # escritos para el contador (son la tabla de decisiones, no un detalle
            # interno): se dejan pasar tal cual dentro del 409.
            raise DecisionNoAplicaError(str(exc), partida_id=partida_id) from exc

        nuevas = [resuelta if p.id == partida_id else p for p in estado.partidas]
        # Se CONSERVA el sello guardado, no se recalcula (ver abajo). Existe con certeza:
        # para llegar acá hubo que encontrar la partida, y las partidas solo existen si
        # alguien concilió.
        sello = await self._repo.huella(case_id)
        assert sello is not None  # hay partidas, así que hay cruce sellado
        # Sin volver a pasar por `refrescar`: no cambiaron ni las cifras ni los documentos,
        # solo la decisión sobre una partida. Reconstruir acá reemplazaría las demás
        # provisionales sin razón y movería su `cuando`.
        revision = await self._repo.reemplazar_partidas(
            case_id,
            partidas=nuevas,
            huerfanas=estado.huerfanas,
            revision_esperada=estado.revision,
            # Se CONSERVA la huella guardada, no se recalcula. Resolver no re-deriva el
            # cruce, así que volver a sellar los renglones con los documentos de hoy
            # afirmaría que salieron de ellos: si entró algo por otra puerta, resolver un
            # renglón cualquiera borraba la marca de "hay que conciliar" y devolvía el
            # agujero por la puerta de atrás. Medido.
            huella_documentos=sello,
        )
        nuevo = self._construir_estado(
            partidas=nuevas,
            huerfanas=estado.huerfanas,
            detail=await self._detalle(case_id),
            revision=revision,
            # El veredicto se hereda del estado que se leyó: resolver no re-deriva el cruce,
            # así que no puede volver correspondiente algo que no lo era.
            corresponde=estado.corresponde_al_expediente,
        )
        await self._recalcular(case_id, nuevo)
        return resuelta, nuevo

    async def peticiones(self, case_id: UUID) -> list[Peticion]:
        """Lo que falta pedirle al cliente, priorizado. Derivado, nunca almacenado."""
        estado = await self.estado(case_id)
        detail = await self._detalle(case_id)
        respuestas = await self._repo.respuestas(case_id)
        caso = estado.caso if estado.caso is not None else self._caso_vacio(detail)
        return derivar_peticiones(estado.partidas, respuestas, caso, p=self._parametros(detail))

    async def recomendaciones(self, case_id: UUID) -> Recomendaciones:
        """Qué le ahorraría cada beneficio, esté o no pedido. Derivado, nunca almacenado.

        Comparte todo con `peticiones` menos el filtro: la cola descarta lo ya contestado, y esto
        recorre el catálogo completo. Es la diferencia entre "qué falta pedir" y "cuánta plata hay
        en juego", y la segunda pregunta no tenía respuesta en ninguna pantalla.
        """
        estado = await self.estado(case_id)
        detail = await self._detalle(case_id)
        respuestas = await self._repo.respuestas(case_id)
        caso = estado.caso if estado.caso is not None else self._caso_vacio(detail)
        return derivar_recomendaciones(
            estado.partidas, respuestas, caso, p=self._parametros(detail)
        )

    async def respuestas(self, case_id: UUID) -> list[Respuesta]:
        """Lo que ya se contestó, para poder verlo y cambiarlo.

        Sin esto, contestar apaga la pregunta y no queda nada a la vista: un "no" dado por
        error es irrecuperable desde la interfaz, y el que revise después no puede saber si una
        deducción falta porque no se preguntó o porque el cliente dijo que no la tenía. Son dos
        situaciones distintas y llevan a decisiones distintas.
        """
        await self._detalle(case_id)
        return await self._repo.respuestas(case_id)

    async def registrar_respuesta(
        self,
        case_id: UUID,
        *,
        pregunta: str,
        tiene: bool,
        quien: str,
        detalle: dict[str, object] | None = None,
        cuando: object = None,
    ) -> list[Peticion]:
        """Guarda lo que contestó el cliente y devuelve la lista que queda.

        Un `no` apaga la petición PARA SIEMPRE: sin este registro el sistema le pregunta
        por prepagada en cada consulta. Justo por eso queda en la bitácora: apagar una
        deducción cambia la declaración, y un cambio así no puede no dejar rastro.
        """
        from datetime import UTC, datetime

        await self._detalle(case_id)
        await self._repo.registrar_respuesta(
            case_id,
            Respuesta(
                pregunta=pregunta,
                tiene=tiene,
                detalle=detalle or {},
                quien=quien,
                cuando=datetime.now(tz=UTC),
            ),
        )
        await self._cases.add_event(
            case_id=case_id,
            kind="ANSWER_RECORDED",
            message=(
                f"{'Sí' if tiene else 'No'} tiene {etiqueta_de_pregunta(pregunta)} "
                f"(lo respondió {quien})"
            ),
            payload={"pregunta": pregunta, "tiene": tiene, "quien": quien},
        )
        return await self.peticiones(case_id)

    async def cerrar_peticion(
        self, case_id: UUID, peticion_id: str, *, quien: str
    ) -> tuple[Peticion, list[Peticion]]:
        """Cierra una petición sin soporte y devuelve lo que cuesta cerrarla.

        Es el MISMO mecanismo que un `no` del cliente (una `Respuesta` con `tiene=False`
        sobre la clave de la petición): dos formas de apagar la misma petición acabarían
        discrepando sobre cuál sigue viva.
        """
        antes = await self.peticiones(case_id)
        cerrada = next((p for p in antes if p.id == peticion_id), None)
        if cerrada is None:
            raise PeticionNoEncontradaError(peticion_id=peticion_id, case_id=str(case_id))
        quedan = await self.registrar_respuesta(
            case_id,
            pregunta=peticion_id,
            tiene=False,
            quien=quien,
            detalle={"cerrada_sin_soporte": True, "tipo_documento": cerrada.tipo_documento},
        )
        return cerrada, quedan

    async def liquidaciones(self, case_id: UUID) -> Liquidaciones:
        """El preliminar, la de hoy y la ganancia entre las dos.

        Sirve las versiones GUARDADAS (el preliminar es una foto que no se recalcula), pero
        dice si la última todavía corresponde al estado de hoy. Que esto responda 200 con
        una `actual` rancia marcada, mientras `/borrador` responde 409, no es una
        inconsistencia: acá hay datos que existen (las versiones), allá hace falta un caso
        que no se puede armar.
        """
        estado = await self.estado(case_id)
        versiones = await self._repo.versiones(case_id)
        if not versiones:
            raise LiquidacionNoDisponibleError(
                estado.falta or LiquidacionNoDisponibleError.default_message,
                case_id=str(case_id),
            )
        de_hoy = self._liquidar_estado(estado)
        vigente = de_hoy is not None and de_hoy == versiones[-1].liquidacion
        return Liquidaciones(
            preliminar=versiones[0],
            actual=versiones[-1],
            vigente=vigente,
            falta=None if vigente else estado.falta,
        )

    async def borrador(self, case_id: UUID) -> str:
        estado, liquidacion = await self._de_hoy(case_id)
        assert estado.caso is not None  # `_de_hoy` ya se negó si no
        return borrador_html(liquidacion, estado.caso)

    async def memoria(self, case_id: UUID) -> str:
        estado, liquidacion = await self._de_hoy(case_id)
        assert estado.caso is not None  # `_de_hoy` ya se negó si no
        return memoria_markdown(liquidacion, estado.caso)

    async def formulario(self, case_id: UUID) -> list[Casilla]:
        """Las casillas del 210 con lo que se va a declarar hoy.

        Se calcula sobre el estado de HOY y no sobre la última versión guardada, por la misma
        razón que el cierre: un formulario fechado con la cifra de antes del último documento es
        un formulario que ya nadie va a radicar.
        """
        estado, liquidacion = await self._de_hoy(case_id)
        if estado.caso is None:
            raise LiquidacionNoDisponibleError(
                estado.falta or LiquidacionNoDisponibleError.default_message,
                case_id=str(case_id),
            )
        return formulario_210(liquidacion, estado.caso)

    async def comparacion_con_la_dian(self, case_id: UUID) -> Comparacion210:
        """El borrador que la DIAN precargó contra el que sale del cálculo, casilla por casilla.

        Es donde se ve qué aportó el trabajo con los documentos, y sobre todo lo contrario: una
        casilla nuestra MENOR que la de la DIAN sin razón registrada es un ingreso que se perdió,
        y es justo lo que la DIAN cruza sola.
        """
        detail = await self._detalle(case_id)
        nuestras = await self.formulario(case_id)
        return comparar(
            nuestras,
            _del_expediente(detail, DocumentType.SUGGESTED_RETURN),
            Contra.BORRADOR_DE_LA_DIAN,
        )

    async def comparacion_con_lo_presentado(self, case_id: UUID) -> Comparacion210:
        """El cálculo contra la declaración que de verdad se presentó ese año.

        ES LA SEGUNDA OPINIÓN. En un año ya declarado, lo presentado es casi siempre el trabajo de
        un contador, así que cada diferencia es una de dos cosas y hay que poder distinguirlas: un
        beneficio que él no tomó (plata que el cliente dejó sobre la mesa) o un error nuestro.

        En el año en curso no hay nada presentado y la comparación sale no disponible, que es lo
        correcto: no existe todavía.
        """
        detail = await self._detalle(case_id)
        nuestras = await self.formulario(case_id)
        return comparar(
            nuestras,
            _del_expediente(detail, DocumentType.FILED_RETURN),
            Contra.DECLARACION_PRESENTADA,
        )

    async def cerrar_borrador(self, case_id: UUID) -> Case:
        """Da el borrador por listo. Se NIEGA si no se puede calcular, o si hay bloqueante.

        Es la mitad "no permitir cerrar" de que `bloqueante` bloquee de verdad: la
        liquidación se puede ver —el borrador es donde el contador lee qué le falta— pero
        no darse por buena. Cerrar con un ingreso por fuera sería dar por completo un
        formulario incompleto.

        LA LIQUIDACIÓN SE CALCULA SOBRE EL ESTADO DE HOY, no sobre la última versión
        guardada (F1). Mirar la guardada tenía dos consecuencias, las dos medidas: se cerraba
        un borrador que `/borrador` se niega a imprimir —fechando el evento con la cifra de
        antes del último documento—, y un bloqueante que aparece DESPUÉS de la última versión
        (una republicación con honorarios deja el caso sin armar, así que no hay versión
        nueva) no se calculaba nunca, justo en el único momento en que se calcularía. El
        aviso que existe para impedir cerrar no puede depender de que ya esté guardado.
        """
        estado, liquidacion = await self._de_hoy(case_id)
        vivos = bloqueantes(liquidacion)
        if vivos:
            raise LiquidacionBloqueadaError(
                detalles=[{"codigo": f.codigo, "mensaje": f.mensaje} for f in vivos]
            )
        # SE REVALIDAN LAS DOS COSAS, y hacen falta las dos: la revisión detecta que alguien
        # resolvió un renglón, y el sello detecta que entró un documento por un camino que no
        # corre el cruce — que es JUSTAMENTE lo único que la revisión no mueve. Con solo la
        # revisión, un 220 que entra en esta ventana dejaba el cierre en 200 y fechado con la
        # cifra anterior al documento (medido).
        fresco = await self.estado(case_id)
        if not fresco.corresponde_al_expediente:
            raise LiquidacionNoDisponibleError(
                fresco.falta or LiquidacionNoDisponibleError.default_message,
                case_id=str(case_id),
            )
        if fresco.revision != estado.revision:
            # Alguien resolvió un renglón mientras se revisaba: la liquidación que se acabó
            # de aprobar ya no es la del expediente. Persistirla sería fechar una versión con
            # cifras viejas, y cerrar sobre ella sería dar por buena una declaración que
            # nadie miró. (La otra mitad de esta carrera la cubre la invalidación del cierre:
            # si el otro gana, el `DRAFT_READY` se cae solo.)
            raise ConflictoDeConcurrenciaError(revision_leida=estado.revision)
        # Se guarda antes de cerrar para que el evento feche la cifra que se dio por buena.
        await self._recalcular(case_id, estado)
        versiones = await self._repo.versiones(case_id)
        actual = versiones[-1]
        caso = await self._cases.transition(case_id, status=CaseStatus.DRAFT_READY)
        await self._cases.add_event(
            case_id=case_id,
            kind="DRAFT_READY",
            message="El borrador del 210 quedó listo para revisión final",
            payload={"version": actual.version, "impuesto": actual.impuesto},
        )
        return caso

    async def incorporar_documentos(
        self, case_id: UUID, subidos: Sequence[Subido]
    ) -> list[ArchivoIncorporado]:
        """Cruza los archivos que ACABAN de entrar al expediente y recalcula.

        Cada `Subido` viene ya guardado y leído por el servicio del expediente, con el SHA
        con que quedó: la identidad va por ese SHA y no por el nombre, porque dos archivos
        homónimos en la misma request son dos documentos distintos. Acá no se lee nada: la
        lectura ya está adjunta al documento (y por eso este camino no necesita otro
        `run_in_threadpool`).

        Si la declaración todavía no se ha conciliado, los archivos NO arrancan el cruce
        solos: quedan guardados y entran cuando alguien concilie. Refrescar sin nada
        guardado no refresca nada, y hacer que subir el reporte de terceros dispare por su
        cuenta la conciliación convertiría un endpoint de archivos en el que decide cuándo
        empieza el trabajo.
        """
        if not await self._repo.guardadas(case_id):
            await self._detalle(case_id)
            return [
                ArchivoIncorporado(
                    archivo=s.archivo,
                    doc_type=s.doc_type,
                    estado=ESTADO_NO_RECIBIDO if s.sha is None else ESTADO_A_BANDEJA,
                    peticion_cerrada=None if s.peticion_id is None else False,
                    motivo=s.motivo or _MOTIVO_SIN_CONCILIAR,
                )
                for s in subidos
            ]
        estado = await self._reconstruir(case_id)
        await self._asegurar_preliminar(case_id)
        await self._recalcular(case_id, estado)
        detail = await self._detalle(case_id)
        # La identidad de cada archivo es su SHA, que llega por índice desde quien lo
        # guardó (F5). Indexar por NOMBRE hacía que dos archivos homónimos en la misma
        # request —`certificado.pdf` y `certificado.pdf`— recibieran los dos el desenlace
        # del último: al contador se le informaba que el 220 de su empleador no cruzó
        # cuando acababa de abrir la discrepancia que ahora tiene que decidir.
        lecturas = {d.content_sha256[:12]: d.reading for d in detail.documents}
        vivas = await self.peticiones(case_id)
        abiertas = {p.id for p in vivas}

        resultados: list[ArchivoIncorporado] = []
        for s in subidos:
            if s.sha is None:
                # No se pudo guardar: no hay documento que cruzar y decir "a bandeja"
                # afirmaría que quedó en la declaración cuando no quedó.
                estado_archivo, motivo = ESTADO_NO_RECIBIDO, s.motivo
            else:
                estado_archivo, motivo = self._desenlace(
                    estado.partidas, s.doc_type, s.sha, lecturas.get(s.sha)
                )
            resultados.append(
                ArchivoIncorporado(
                    archivo=s.archivo,
                    doc_type=s.doc_type,
                    estado=estado_archivo,
                    # Cerrada = ya no aparece en la lista derivada. No se declara
                    # "cumplida" por haber llegado el archivo: lo que cuenta es que la
                    # petición efectivamente desapareció.
                    peticion_cerrada=(
                        None if s.peticion_id is None else s.peticion_id not in abiertas
                    ),
                    motivo=motivo,
                )
            )
        return resultados

    # ─────────────────────────── internos ───────────────────────────

    async def _reconstruir(self, case_id: UUID) -> Estado:
        """`abrir` la exógena vigente + reincorporar TODO + `refrescar` con lo guardado.

        Nunca por el camino corto (`incorporar` sobre las partidas resueltas que hay): una
        provisional que quede pegada tras llegar un documento nuevo declara la cifra vieja
        y ESCONDE la discrepancia.
        """
        detail = await self._detalle(case_id)
        exogena = self._exogena_vigente(detail)
        if exogena is None:
            raise SinReporteDeTercerosError(case_id=str(case_id))

        nuevas = abrir(exogena)
        for lectura in self._cruzables(detail):
            nuevas = incorporar(nuevas, lectura)

        # La revisión se lee ANTES de derivar: es la precondición de la escritura de abajo,
        # y el trabajo que va en medio (el cruce completo) es justo la ventana en la que
        # otra request puede colarse. "El contador decide mientras entra un documento"
        # perdía una de las dos cosas por acá, no solo por `resolver_partida`.
        revision_leida = await self._repo.revision(case_id)
        vivas_antes = await self._repo.vivas(case_id)
        guardadas = await self._repo.guardadas(case_id)
        partidas, huerfanas = refrescar(nuevas, guardadas)
        descartadas = self._resoluciones_descartadas(guardadas, partidas)
        # Los renglones quedan sellados con los documentos de los que salieron: es lo que
        # después permite saber si siguen correspondiendo al expediente. No puede ser None
        # acá: hay exógena vigente, verificado arriba.
        sello = self._huella_documentos(detail)
        assert sello is not None
        revision = await self._repo.reemplazar_partidas(
            case_id,
            partidas=partidas,
            huerfanas=huerfanas,
            revision_esperada=revision_leida,
            huella_documentos=sello,
        )
        await self._registrar_descartadas(case_id, descartadas)
        if not self._mismos_renglones(vivas_antes, partidas):
            await self._invalidar_cierre(case_id)
        return self._construir_estado(
            partidas=partidas,
            huerfanas=huerfanas,
            detail=detail,
            revision=revision,
            # Acaba de derivarse del expediente y quedó sellado con él.
            corresponde=True,
        )

    def _mismos_renglones(self, antes: Sequence[Partida], despues: Sequence[Partida]) -> bool:
        """¿Los renglones dicen lo mismo? Por id y sin mirar CUÁNDO se resolvieron.

        Comparar las listas tal cual daba SIEMPRE distinto, por dos razones independientes y
        las dos medidas: vienen en órdenes distintos (las guardadas ordenadas por id, las
        derivadas en orden de derivación) y las provisionales del sistema se re-derivan con
        `cuando` fresco aunque el resto del dump sea idéntico. Resultado: `POST /conciliacion`
        —idempotente por contrato— tumbaba el cierre con un evento que decía "la declaración
        cambió" cuando no había cambiado nada, y dejaba `DRAFT_READY` inservible.

        `cuando` se excluye porque una provisional re-derivada con la misma decisión, el mismo
        valor y la misma huella ES la misma conclusión: solo cambió el reloj.
        """

        def por_id(ps: Sequence[Partida]) -> dict[str, dict[str, object]]:
            return {p.id: p.model_dump(mode="json", exclude={"resolucion": {"cuando"}}) for p in ps}

        return por_id(antes) == por_id(despues)

    async def _invalidar_cierre(self, case_id: UUID) -> None:
        """El borrador deja de estar listo cuando la declaración cambia bajo sus pies.

        `DRAFT_READY` era terminal de hecho: nada lo invalidaba, así que quedaba un
        expediente marcado "listo para revisión final" con renglones que ya no
        correspondían — la misma mentira del guard que falla abierto, pero persistida en el
        estado del expediente. Se vuelve a `READY_FOR_REVIEW` y queda el evento: el cierre
        hay que volver a ganárselo, y quien audite ve que se perdió y por qué.

        El estado se lee FRESCO, no se recibe. Tomarlo del `detail` que el llamador leyó al
        empezar su trabajo hacía que la invalidación NO disparara justo cuando el cierre había
        ocurrido en medio: en la base el caso ya era `DRAFT_READY` y acá se veía como si no lo
        fuera, así que la red de seguridad no existía por ese camino.
        """
        caso = await self._cases.get(case_id)
        if caso is None or caso.status is not CaseStatus.DRAFT_READY:
            return
        await self._cases.transition(case_id, status=CaseStatus.READY_FOR_REVIEW)
        await self._cases.add_event(
            case_id=case_id,
            kind="DRAFT_READY_INVALIDADO",
            message=(
                "El borrador dejó de estar listo: la declaración cambió después de que se "
                "dio por buena y hay que revisarla otra vez."
            ),
        )
        log.info("conciliacion.cierre_invalidado", case_id=str(case_id))

    def _huella_documentos(self, detail: CaseDetail) -> str | None:
        """La identidad del conjunto de insumos del que salen los renglones.

        Es lo que permite saber, sin re-derivar nada, si los renglones persistidos siguen
        correspondiendo al expediente. Cubre la exógena vigente y TODOS los documentos que
        el conciliador sabe cruzar, EN ORDEN: el orden es parte de la entrada del cruce (con
        dos certificados rivales del mismo empleador rige el último nuevo), así que un
        reordenamiento también invalida los renglones.

        None cuando no hay ningún insumo. Se distingue de una huella vacía a propósito: "no
        hay nada que cruzar" y "hay documentos que no se han cruzado" llevan a acciones
        distintas.
        """
        exogena = self._exogena_vigente(detail)
        cruzables = [d.content_sha256 for d in self._documentos_cruzables(detail)]
        if exogena is None and not cruzables:
            return None
        semilla = json.dumps(
            {"exogena": exogena.content_sha256 if exogena else None, "cruzables": cruzables},
            separators=(",", ":"),
        )
        return hashlib.sha256(semilla.encode()).hexdigest()

    def _exogena_vigente(self, detail: CaseDetail) -> DocumentReading | None:
        """La lectura del reporte de terceros que rige hoy.

        Con varias (el portal reconsulta, el contador vuelve a subirla) manda la más
        reciente, la misma doctrina con que el expediente reemplaza los documentos del
        portal: acumular copias dejaría el cruce sin un insumo vigente claro.
        """
        candidatas = [
            d for d in detail.documents if d.doc_type == DOC_TYPE_EXOGENA and d.reading is not None
        ]
        if not candidatas:
            return None
        return max(candidatas, key=lambda d: (d.added_at, str(d.id))).reading

    def _documentos_cruzables(self, detail: CaseDetail) -> list[CaseDocument]:
        """Los documentos que el conciliador sabe cruzar, en orden de llegada.

        El orden importa y es el de llegada: con dos certificados rivales del mismo
        empleador rige el último NUEVO, así que reconstruir en otro orden cambiaría la
        cifra publicada. Una sola definición del conjunto y su orden, porque la usan las dos
        cosas que tienen que coincidir: la reconstrucción del cruce y la huella con que se
        comprueba si los renglones siguen correspondiendo al expediente.
        """
        return [
            d
            for d in sorted(detail.documents, key=lambda x: (x.added_at, str(x.id)))
            if d.reading is not None and d.doc_type in TIPO_A_CLAVE
        ]

    def _cruzables(self, detail: CaseDetail) -> list[DocumentReading]:
        """Las lecturas de esos documentos, en el mismo orden."""
        lecturas = [d.reading for d in self._documentos_cruzables(detail)]
        return [r for r in lecturas if r is not None]

    def _desenlace(
        self,
        partidas: Sequence[Partida],
        doc_type: str,
        sha: str | None,
        lectura: DocumentReading | None,
    ) -> tuple[str, str | None]:
        """Qué le pasó a un archivo, derivado del estado real del cruce.

        No se afirma "emparejado" por haber llegado: se busca el sha del documento en las
        versiones de las partidas y se mira si la partida que lo recibió tiene lado DIAN.
        """
        if doc_type not in TIPO_A_CLAVE:
            return ESTADO_A_BANDEJA, _MOTIVO_A_BANDEJA
        if lectura is None:
            return ESTADO_A_BANDEJA, _MOTIVO_SIN_LECTURA
        tocadas = [p for p in partidas if sha is not None and sha in p.versiones_documento]
        if not tocadas:
            # El tipo se sabe cruzar pero la lectura no trajo ninguno de sus campos.
            return ESTADO_A_BANDEJA, _MOTIVO_SIN_LECTURA
        if any(p.version_dian is not None for p in tocadas):
            return ESTADO_EMPAREJADO, None
        return ESTADO_SIN_EMPAREJAR, _MOTIVO_SIN_EMPAREJAR

    async def _recalcular(self, case_id: UUID, estado: Estado) -> None:
        """Guarda una versión nueva de la liquidación si cambió algo.

        Solo si CAMBIÓ: una versión por request llenaría la historia de filas idénticas y
        la ganancia dejaría de significar nada. La comparación es sobre la liquidación
        completa (cifras y avisos), no sobre el impuesto: un aviso bloqueante nuevo con el
        mismo impuesto también es un cambio que hay que poder fechar.
        """
        if estado.caso is None or not estado.corresponde_al_expediente:
            # Explícito además de implícito (`_construir_estado` ya deja `caso=None` cuando
            # no corresponde): persistir una versión derivada de renglones viejos mete al
            # historial una cifra calculada sin un documento que ya está en el expediente.
            return
        versiones = await self._repo.versiones(case_id)
        candidata = liquidar_y_versionar(
            estado.caso,
            estado.partidas,
            p=self._parametros_de(estado.caso.anio_gravable),
            version=len(versiones) + 1,
            avisos_extra=estado.avisos_beneficios,
        )
        if versiones and versiones[-1].liquidacion == candidata.liquidacion:
            return
        await self._repo.agregar_version(case_id, candidata)
        # Una liquidación nueva es un cambio en la declaración: si estaba dada por lista,
        # deja de estarlo. Cubre el camino que `_reconstruir` no ve — resolver un renglón no
        # cambia el conjunto de documentos, pero sí cambia el 210.
        await self._invalidar_cierre(case_id)

    def _resoluciones_descartadas(
        self, guardadas: Sequence[Partida], partidas: Sequence[Partida]
    ) -> list[Partida]:
        """Las decisiones de una PERSONA que la re-derivación acaba de invalidar (F6).

        `refrescar` invalida una resolución del contador cuando las cifras cambiaron, y lo
        señala con la `nota` — que es texto libre y que el siguiente rebuild vuelve a
        derivar desde cero: medido, a la segunda reconstrucción la nota desaparece y queda
        un renglón resuelto por el sistema, fuera de la cola, sin huérfana y sin rastro. La
        plata declarada es la conservadora, así que no hay cifra mala; lo que se pierde es
        la auditoría de una decisión humana, la misma clase de pérdida que T4 cerró
        volviendo ESTRUCTURAL la marca de plata ajena.

        Se detecta desde afuera, sin tocar `refrescar`: una guardada con resolución de
        CONTADOR cuyo id SIGUE existiendo pero que ya no la tiene. Las que desaparecieron
        son huérfanas y viajan por el otro canal.
        """
        nuevas = {p.id: p for p in partidas}
        descartadas = []
        for guardada in guardadas:
            previa = guardada.resolucion
            if previa is None or previa.origen is not Origen.CONTADOR:
                continue
            nueva = nuevas.get(guardada.id)
            if nueva is None:
                continue  # desapareció: es huérfana, va por el otro canal
            if nueva.resolucion is None or nueva.resolucion.origen is not Origen.CONTADOR:
                descartadas.append(guardada)
        return descartadas

    async def _registrar_descartadas(self, case_id: UUID, descartadas: Sequence[Partida]) -> None:
        """Deja la decisión descartada como HECHO, no como texto que el próximo rebuild borra.

        Dos registros, cada uno con su razón de ser: un evento en la bitácora (append-only y
        fechado: es la auditoría de que una persona decidió y su decisión se cayó) y una
        alerta abierta (para que aparezca en lo que el contador tiene que revisar y pueda
        cerrarla, en vez de vivir solo en un log). Se escribe UNA vez: al siguiente rebuild
        la partida ya trae resolución del sistema, no de CONTADOR, y no se vuelve a detectar.
        """
        for partida in descartadas:
            previa = partida.resolucion
            assert previa is not None  # filtrado en `_resoluciones_descartadas`
            resumen = f"{previa.decision} por {previa.valor:,} pesos, decidida por {previa.quien}"
            await self._cases.add_event(
                case_id=case_id,
                kind="RESOLUCION_DESCARTADA",
                message=(
                    f"Se descartó una decisión sobre {partida.id} ({resumen}): los valores "
                    "cambiaron y hay que volver a decidir."
                ),
                payload={
                    "partida_id": partida.id,
                    "decision": previa.decision.value,
                    "motivo": previa.motivo.value,
                    "valor": previa.valor,
                    "quien": previa.quien,
                    "cuando": previa.cuando.isoformat(),
                    "huella": previa.huella,
                },
            )
            await self._cases.add_flag(
                case_id=case_id,
                code="RESOLUCION_DESCARTADA",
                message=(
                    f"La decisión sobre {partida.id} ({resumen}) quedó sin efecto porque "
                    "las cifras cambiaron. Hay que volver a decidir ese renglón."
                ),
                severity=FlagSeverity.WARNING,
            )
            log.info(
                "conciliacion.resolucion_descartada",
                case_id=str(case_id),
                partida_id=partida.id,
                quien=previa.quien,
            )

    async def _asegurar_preliminar(self, case_id: UUID) -> None:
        """La versión 1 se liquida SIEMPRE desde la exógena sola (F4).

        El orden natural del producto es que el cliente mande el certificado por chat y el
        contador concilie después. Con el preliminar definido como "la primera versión que
        se pudo guardar", ese orden lo dejaba naciendo con el 220 ya dentro y la ganancia en
        0 para siempre: desaparecían justo las cifras que el producto existe para mostrar.
        El preliminar es la foto de lo que la DIAN sabía, no la de cuándo pudimos calcular.

        Si la exógena sola NO se puede liquidar (trae honorarios, o una fila ajena: nada de
        eso lo resuelve el automatismo), no se fuerza nada — `_recalcular` pondrá la versión
        1 con lo que haya y `base_sin_documentos` en False dirá que la ganancia subestima.
        Residuo asumido: si más tarde el contador resuelve ese renglón, el preliminar sigue
        siendo el que se pudo guardar; re-fechar la versión 1 hacia atrás sería reescribir
        una foto.
        """
        if await self._repo.versiones(case_id):
            return
        detail = await self._detalle(case_id)
        exogena = self._exogena_vigente(detail)
        if exogena is None:  # pragma: no cover - `_reconstruir` ya se habría negado
            return
        solo_dian = autorresolver(abrir(exogena))
        # El preliminar es la foto de la exógena SOLA, así que va sin beneficios: los
        # certificados del cliente son justamente lo que todavía no llegó.
        caso, _falta = self._intentar_caso(solo_dian, detail, Beneficios())
        if caso is None:
            return
        await self._repo.agregar_version(
            case_id,
            liquidar_y_versionar(
                caso,
                solo_dian,
                p=self._parametros_de(caso.anio_gravable),
                version=1,
                base_sin_documentos=True,
            ),
        )

    def _liquidar_estado(self, estado: Estado) -> Liquidacion | None:
        """La liquidación del estado tal como está, o None si el caso no se puede armar."""
        if estado.caso is None:
            return None
        return liquidar_conciliado(
            estado.caso,
            estado.partidas,
            self._parametros_de(estado.caso.anio_gravable),
            estado.avisos_beneficios,
        )

    async def _de_hoy(self, case_id: UUID) -> tuple[Estado, Liquidacion]:
        """El estado de hoy y su liquidación, o 409 diciendo qué falta para calcularla.

        No sirve la última versión guardada: puede ser de antes del último cambio, y
        rendirla junto al caso de hoy mezclaría las cifras de dos momentos. El guard NO es
        inalcanzable —es el que produce el 409 de `/borrador` en cuanto llega un documento
        que deja renglones por decidir—; el `pragma: no cover` que tenía era falso y fue lo
        que dejó creer que `cerrar_borrador` no lo necesitaba (F1).
        """
        estado = await self.estado(case_id)
        liquidacion = self._liquidar_estado(estado)
        if liquidacion is None:
            raise LiquidacionNoDisponibleError(
                estado.falta or LiquidacionNoDisponibleError.default_message,
                case_id=str(case_id),
            )
        return estado, liquidacion

    @staticmethod
    def _movimientos(detail: CaseDetail) -> Movimientos:
        """Los insumos del chequeo de obligación, de la exógena vigente del expediente."""
        for d in detail.documents:
            if d.doc_type == "EXOGENA" and d.reading is not None:
                return movimientos_de(d.reading)
        return Movimientos()

    def _intentar_caso(
        self, partidas: Sequence[Partida], detail: CaseDetail, beneficios: Beneficios
    ) -> tuple[CasoTributario | None, str | None]:
        """El caso que el motor liquida, o el motivo por el que todavía no se puede armar.

        Los tres motivos son distintos y se distinguen a propósito: quedan renglones sin
        decidir (lo normal, y `a_caso` lo dice con el conteo), hay un concepto que el motor
        no modela (hay que llevarlo a mano), o el caso salió con una cifra imposible. El
        tercero NO hace eco del mensaje de pydantic: ese texto habla de campos del modelo y
        quien lee esto es un contador.

        EL ORDEN DE LOS `except` ES LOAD-BEARING (F3): `pydantic.ValidationError` HEREDA de
        `ValueError`, así que con `ValueError` arriba la rama de pydantic era código muerto y
        su mensaje de producto no salía nunca — el que salía era el crudo del validador
        ("Input should be greater than or equal to 0 … errors.pydantic.dev"), en el cuerpo
        200 de conciliar y en el 409 de la liquidación, el borrador y la memoria. Un
        `except ValueError` puesto antes vuelve a cerrar esa rama sin que nada falle.
        """
        try:
            return (
                a_caso(
                    list(partidas),
                    contribuyente=self._contribuyente(detail),
                    anio_gravable=detail.case.tax_year,
                    # Los beneficios NO salen del cruce: la DIAN no sabe que alguien paga
                    # medicina prepagada, y ahí está su valor. Sin esta línea los cinco
                    # certificados de beneficio se leen, se paga la llamada al modelo, y no
                    # se declaran.
                    beneficios=beneficios,
                    # Tampoco salen del cruce, y por la razón opuesta: sus filas no abren
                    # partida porque no se declaran en ninguna casilla. Pero el motor los
                    # necesita para saber si la persona está obligada, así que llegan por acá
                    # en vez de desaparecer.
                    movimientos=self._movimientos(detail),
                ),
                None,
            )
        except PydanticValidationError as exc:
            campos = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
            log.warning("conciliacion.caso_invalido", campos=campos)
            return None, (
                "Alguna de las cifras resueltas no puede entrar a la declaración (por "
                "ejemplo un valor negativo). Hay que revisar los renglones resueltos "
                "antes de calcular."
            )
        except NotImplementedError as exc:
            return None, str(exc)
        except ValueError as exc:
            return None, str(exc)

    def _caso_vacio(self, detail: CaseDetail) -> CasoTributario:
        """Un caso sin hechos, para poder derivar peticiones cuando todavía no hay 210.

        Sin esto, una declaración con renglones pendientes no tendría lista de peticiones
        — que es justo lo que hace falta para que deje de tenerlos.
        """
        return CasoTributario(
            anio_gravable=detail.case.tax_year, contribuyente=self._contribuyente(detail)
        )

    def _contribuyente(self, detail: CaseDetail) -> Contribuyente:
        """El titular, con el nombre que se sepa.

        El nombre sale del cliente y, si no lo tiene, del reporte de la DIAN; y si tampoco,
        de su identificación. Va impreso en el borrador, así que no puede quedar vacío.
        """
        del_reporte = self._exogena_vigente(detail)
        nombre = detail.client.full_name or (
            str(del_reporte.field("taxpayer_name")) if del_reporte is not None else ""
        )
        return Contribuyente(
            tipo_doc=detail.client.id_kind.value,
            num_doc=detail.client.id_number,
            nombre=nombre.strip() or detail.client.id_number,
        )

    def _parametros(self, detail: CaseDetail) -> ParametrosAnio:
        return self._parametros_de(detail.case.tax_year)

    def _parametros_de(self, anio: int) -> ParametrosAnio:
        """Los parámetros del año gravable, o un 409 que se puede accionar (F8).

        `OpenCaseRequest` acepta expedientes desde 2015 y el repo solo trae los YAML de los
        años calibrados: sin esta traducción, conciliar un 2019 daba 500 con el texto crudo
        del cargador. No es un fallo del servidor — es un año que todavía no se liquida.
        """
        try:
            return cargar(anio)
        except ValueError as exc:
            raise AnioSinParametrosError(anio=anio) from exc

    async def _detalle(self, case_id: UUID) -> CaseDetail:
        detail = await self._cases.get_detail(case_id)
        if detail is None:
            raise CaseNotFoundError(case_id=str(case_id))
        return detail


def flags_de(liquidacion: LiquidacionVersionada) -> list[Flag]:
    """Los avisos de una versión, para las respuestas HTTP."""
    return list(liquidacion.liquidacion.flags)


def decisiones_posibles(partida: Partida) -> dict[str, list[str]]:
    """Qué puede decidir el contador sobre este renglón, y con qué motivo cada cosa.

    SE DERIVA PREGUNTÁNDOLE A `resolver`, no copiando su tabla. La tabla de decisiones
    (estado × decisión × concepto × motivo) pasó por seis rondas de revisión y tiene reglas
    que no se ven desde afuera: `LLEVAR_A_MANO` solo aplica a los conceptos que el motor no
    liquida, `USAR_DIAN` necesita que exista lado DIAN, y cada decisión admite unos motivos
    y no otros. Una segunda copia acá —o en el front— se desincronizaría en el primer
    cambio, y la interfaz ofrecería una decisión que el backend rechaza (o peor: escondería
    la única salida que un renglón tiene).

    El precio es llamar a `resolver` una vez por combinación y botar el resultado. Es puro
    y barato (un `model_copy` y un sha), y compra que esto no pueda mentir.
    """
    posibles: dict[str, list[str]] = {}
    for decision in Decision:
        # `USAR_OTRO` es la única que exige valor explícito; se sondea con 0, que es válido.
        valor = 0 if decision is Decision.USAR_OTRO else None
        motivos = []
        for motivo in Motivo:
            # `CLASIFICAR` exige clase, así que se sondea con cada una: el motivo es posible si
            # alguna clase lo acompaña. Cuál va con cuál lo responde `clases_posibles`.
            clases: tuple[ClaseDeIngreso | None, ...] = (
                tuple(ClaseDeIngreso) if decision is Decision.CLASIFICAR else (None,)
            )
            if any(_acepta(partida, decision, motivo, valor, c) for c in clases):
                motivos.append(motivo.value)
        if motivos:
            posibles[decision.value] = motivos
    return posibles


# Lo que el concepto de la exógena implica sobre la clase, cuando implica algo.
#
# ESTO ES UNA SUGERENCIA Y NO PUEDE SER MÁS QUE ESO. Servicios y honorarios son, en la práctica
# colombiana, el ingreso del independiente que va a rentas de trabajo; pero eso depende de un hecho
# que no está en ningún documento (si imputa costos y si contrató dos o más trabajadores) y que
# solo sabe el contribuyente. Aplicarla sola le daría el 25% exento a alguien que quizá no tiene
# derecho, y eso es inexactitud: sanción del 100% del mayor impuesto más intereses de mora.
#
# `OTROS` no sugiere nada a propósito: es el cajón de sastre de la exógena y puede ser un arriendo,
# un rendimiento o una venta. Adivinar ahí sería inventar.
_CLASE_SUGERIDA: dict[Concepto, ClaseDeIngreso] = {
    Concepto.HONORARIOS: ClaseDeIngreso.RENTA_DE_TRABAJO,
    Concepto.SERVICIOS: ClaseDeIngreso.RENTA_DE_TRABAJO,
}


def clase_sugerida(partida: Partida) -> str | None:
    """La clase que el concepto implica, cuando el renglón se puede clasificar y hay implicación."""
    if partida.concepto is None or partida.concepto not in CONCEPTOS_FUERA_DEL_MOTOR:
        return None
    sugerida = _CLASE_SUGERIDA.get(partida.concepto)
    return sugerida.value if sugerida is not None else None


def clases_posibles(partida: Partida) -> dict[str, list[str]]:
    """Qué clase de ingreso puede afirmar cada motivo de `CLASIFICAR`.

    Va aparte de `decisiones_posibles` para no cambiarle el contrato (decisión → motivos), y se
    deriva del mismo modo: preguntándole a `resolver`. La regla que protege es legal, no de forma —
    `RENTA_DE_TRABAJO` da acceso al 25% exento del art. 206 num. 10 y solo se sostiene con el hecho
    de no imputar costos ni tener dos o más trabajadores. Una copia de esa tabla en el front la
    dejaría ofrecer la clase que más baja el impuesto con cualquier motivo.
    """
    por_motivo: dict[str, list[str]] = {}
    for motivo in Motivo:
        clases = [
            clase.value
            for clase in ClaseDeIngreso
            if _acepta(partida, Decision.CLASIFICAR, motivo, None, clase)
        ]
        if clases:
            por_motivo[motivo.value] = clases
    return por_motivo


def _acepta(
    partida: Partida,
    decision: Decision,
    motivo: Motivo,
    valor: int | None,
    clase: ClaseDeIngreso | None,
) -> bool:
    """Si `resolver` acepta esa combinación. Puro y barato: un `model_copy` y un sha."""
    try:
        resolver(partida, decision, motivo=motivo, quien="sondeo", valor=valor, clase=clase)
    except ValueError:
        return False
    return True


def _del_expediente(detail: CaseDetail, tipo: DocumentType) -> DocumentReading | None:
    """La lectura del documento de ese tipo que esté en el expediente, si se pudo leer.

    Se toma el MÁS RECIENTE: la DIAN recalcula su borrador cuando un tercero corrige la exógena (su
    propia documentación dice que se actualiza a mitad y a final de cada semana durante la
    temporada), así que un expediente puede tener varios y el viejo compararía contra cifras que la
    DIAN ya cambió. Con una declaración presentada aplica lo mismo si hubo corrección.
    """
    candidatos = [d for d in detail.documents if d.doc_type == tipo.value and d.reading is not None]
    if not candidatos:
        return None
    return max(candidatos, key=lambda d: d.added_at).reading
