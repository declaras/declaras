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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from declaras.caso import CasoTributario, Contribuyente
from declaras.documents.models import DocumentReading
from declaras.domain.case import Case, CaseDetail, CaseStatus
from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import (
    CaseNotFoundError,
    DecisionNoAplicaError,
    LiquidacionBloqueadaError,
    LiquidacionNoDisponibleError,
    PartidaNoEncontradaError,
    PeticionNoEncontradaError,
    SinReporteDeTercerosError,
)
from declaras.motor import Flag
from declaras.observability import get_logger
from declaras.parametros import ParametrosAnio, cargar
from declaras.render import borrador_html, memoria_markdown
from declaras.services.conciliacion import (
    TIPO_A_CLAVE,
    Decision,
    LiquidacionVersionada,
    Motivo,
    Partida,
    Peticion,
    Respuesta,
    a_caso,
    abrir,
    bloqueantes,
    derivar_peticiones,
    ganancia,
    incorporar,
    liquidar_y_versionar,
    pendientes,
    refrescar,
    resolver,
)

# El criterio de "cuánta plata depende de que alguien mire esta partida" se importa del
# conciliador a propósito, aunque sea privado: es el mismo con que se ordena la cola de
# pendientes, y una segunda copia haría que la cola del contador y la lista del API se
# ordenaran distinto sin que nadie lo note.
from declaras.services.conciliacion.resolucion import _plata_en_juego

log = get_logger(__name__)

# El tipo con que el reporte de terceros vive en el expediente.
DOC_TYPE_EXOGENA = "EXOGENA"

# Qué le pasó a un archivo que acabó de llegar, desde el punto de vista del cruce.
ESTADO_EMPAREJADO = "emparejado"
ESTADO_SIN_EMPAREJAR = "sin_emparejar"
ESTADO_A_BANDEJA = "a_bandeja"

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

    async def reemplazar_partidas(
        self, case_id: UUID, *, partidas: list[Partida], huerfanas: list[Partida]
    ) -> None: ...

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

    @property
    def ganancia(self) -> int:
        return ganancia(self.preliminar, self.actual)


class ConciliacionService:
    def __init__(
        self, *, cases: CaseRepository, conciliacion: ConciliacionRepository
    ) -> None:
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
        partidas = await self._repo.vivas(case_id)
        huerfanas = await self._repo.huerfanas(case_id)
        caso, falta = self._intentar_caso(partidas, detail)
        return Estado(partidas=partidas, huerfanas=huerfanas, caso=caso, falta=falta)

    async def resolver_partida(
        self,
        case_id: UUID,
        partida_id: str,
        *,
        decision: Decision,
        motivo: Motivo,
        quien: str,
        valor: int | None = None,
        nota: str | None = None,
    ) -> tuple[Partida, Estado]:
        """Registra la decisión del contador sobre un renglón y recalcula."""
        estado = await self.estado(case_id)
        objetivo = next((p for p in estado.partidas if p.id == partida_id), None)
        if objetivo is None:
            raise PartidaNoEncontradaError(partida_id=partida_id, case_id=str(case_id))
        try:
            resuelta = resolver(
                objetivo, decision, motivo=motivo, quien=quien, valor=valor, nota=nota
            )
        except ValueError as exc:
            # El conciliador valida decisión × estado × concepto y sus mensajes ya están
            # escritos para el contador (son la tabla de decisiones, no un detalle
            # interno): se dejan pasar tal cual dentro del 409.
            raise DecisionNoAplicaError(str(exc), partida_id=partida_id) from exc

        nuevas = [resuelta if p.id == partida_id else p for p in estado.partidas]
        # Sin volver a pasar por `refrescar`: no cambiaron ni las cifras ni los documentos,
        # solo la decisión sobre una partida. Reconstruir acá reemplazaría las demás
        # provisionales sin razón y movería su `cuando`.
        await self._repo.reemplazar_partidas(
            case_id, partidas=nuevas, huerfanas=estado.huerfanas
        )
        detail = await self._detalle(case_id)
        caso, falta = self._intentar_caso(nuevas, detail)
        nuevo = Estado(partidas=nuevas, huerfanas=estado.huerfanas, caso=caso, falta=falta)
        await self._recalcular(case_id, nuevo)
        return resuelta, nuevo

    async def peticiones(self, case_id: UUID) -> list[Peticion]:
        """Lo que falta pedirle al cliente, priorizado. Derivado, nunca almacenado."""
        estado = await self.estado(case_id)
        detail = await self._detalle(case_id)
        respuestas = await self._repo.respuestas(case_id)
        caso = estado.caso if estado.caso is not None else self._caso_vacio(detail)
        return derivar_peticiones(
            estado.partidas, respuestas, caso, p=self._parametros(detail)
        )

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
        por prepagada en cada consulta.
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
        """El preliminar, la de hoy y la ganancia entre las dos."""
        estado = await self.estado(case_id)
        versiones = await self._repo.versiones(case_id)
        if not versiones:
            raise LiquidacionNoDisponibleError(
                estado.falta or LiquidacionNoDisponibleError.default_message,
                case_id=str(case_id),
            )
        return Liquidaciones(preliminar=versiones[0], actual=versiones[-1])

    async def borrador(self, case_id: UUID) -> str:
        liquidacion, caso = await self._para_render(case_id)
        return borrador_html(liquidacion.liquidacion, caso)

    async def memoria(self, case_id: UUID) -> str:
        liquidacion, caso = await self._para_render(case_id)
        return memoria_markdown(liquidacion.liquidacion, caso)

    async def cerrar_borrador(self, case_id: UUID) -> Case:
        """Da el borrador por listo. Se NIEGA si hay una alerta bloqueante viva.

        Es la mitad "no permitir cerrar" de que `bloqueante` bloquee de verdad: la
        liquidación se puede ver —el borrador es donde el contador lee qué le falta— pero
        no darse por buena. Cerrar con un ingreso por fuera sería dar por completo un
        formulario incompleto.
        """
        actual = (await self.liquidaciones(case_id)).actual
        vivos = bloqueantes(actual.liquidacion)
        if vivos:
            raise LiquidacionBloqueadaError(
                detalles=[{"codigo": f.codigo, "mensaje": f.mensaje} for f in vivos]
            )
        caso = await self._cases.transition(case_id, status=CaseStatus.DRAFT_READY)
        await self._cases.add_event(
            case_id=case_id,
            kind="DRAFT_READY",
            message="El borrador del 210 quedó listo para revisión final",
            payload={"version": actual.version, "impuesto": actual.impuesto},
        )
        return caso

    async def incorporar_documentos(
        self, case_id: UUID, subidos: Sequence[tuple[str, str, str | None]]
    ) -> list[ArchivoIncorporado]:
        """Cruza los archivos que ACABAN de entrar al expediente y recalcula.

        `subidos` es (nombre del archivo, doc_type, peticion_id) por archivo, ya guardados
        y leídos por el servicio del expediente. Acá no se lee nada: la lectura ya está
        adjunta al documento (y por eso este camino no necesita otro `run_in_threadpool`).

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
                    archivo=archivo,
                    doc_type=doc_type,
                    estado=ESTADO_A_BANDEJA,
                    peticion_cerrada=None if peticion_id is None else False,
                    motivo=_MOTIVO_SIN_CONCILIAR,
                )
                for archivo, doc_type, peticion_id in subidos
            ]
        estado = await self._reconstruir(case_id)
        await self._recalcular(case_id, estado)
        detail = await self._detalle(case_id)
        shas = {
            d.filename: d.content_sha256[:12]
            for d in detail.documents
            if d.superseded_at is None
        }
        lecturas = {d.filename: d.reading for d in detail.documents}
        vivas = await self.peticiones(case_id)
        abiertas = {p.id for p in vivas}

        resultados: list[ArchivoIncorporado] = []
        for archivo, doc_type, peticion_id in subidos:
            estado_archivo, motivo = self._desenlace(
                estado.partidas, doc_type, shas.get(archivo), lecturas.get(archivo)
            )
            resultados.append(
                ArchivoIncorporado(
                    archivo=archivo,
                    doc_type=doc_type,
                    estado=estado_archivo,
                    # Cerrada = ya no aparece en la lista derivada. No se declara
                    # "cumplida" por haber llegado el archivo: lo que cuenta es que la
                    # petición efectivamente desapareció.
                    peticion_cerrada=(
                        None if peticion_id is None else peticion_id not in abiertas
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

        guardadas = await self._repo.guardadas(case_id)
        partidas, huerfanas = refrescar(nuevas, guardadas)
        await self._repo.reemplazar_partidas(
            case_id, partidas=partidas, huerfanas=huerfanas
        )
        caso, falta = self._intentar_caso(partidas, detail)
        return Estado(partidas=partidas, huerfanas=huerfanas, caso=caso, falta=falta)

    def _exogena_vigente(self, detail: CaseDetail) -> DocumentReading | None:
        """La lectura del reporte de terceros que rige hoy.

        Con varias (el portal reconsulta, el contador vuelve a subirla) manda la más
        reciente, la misma doctrina con que el expediente reemplaza los documentos del
        portal: acumular copias dejaría el cruce sin un insumo vigente claro.
        """
        candidatas = [
            d
            for d in detail.documents
            if d.doc_type == DOC_TYPE_EXOGENA and d.reading is not None
        ]
        if not candidatas:
            return None
        return max(candidatas, key=lambda d: (d.added_at, str(d.id))).reading

    def _cruzables(self, detail: CaseDetail) -> list[DocumentReading]:
        """Las lecturas que el conciliador sabe cruzar, en orden de llegada.

        El orden importa y es el de llegada: con dos certificados rivales del mismo
        empleador rige el último NUEVO, así que reconstruir en otro orden cambiaría la
        cifra publicada.
        """
        return [
            d.reading
            for d in sorted(detail.documents, key=lambda x: (x.added_at, str(x.id)))
            if d.reading is not None and d.doc_type in TIPO_A_CLAVE
        ]

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
        if estado.caso is None:
            return
        versiones = await self._repo.versiones(case_id)
        candidata = liquidar_y_versionar(
            estado.caso,
            estado.partidas,
            p=cargar(estado.caso.anio_gravable),
            version=len(versiones) + 1,
        )
        if versiones and versiones[-1].liquidacion == candidata.liquidacion:
            return
        await self._repo.agregar_version(case_id, candidata)

    async def _para_render(self, case_id: UUID) -> tuple[LiquidacionVersionada, CasoTributario]:
        estado = await self.estado(case_id)
        liquidaciones = await self.liquidaciones(case_id)
        if estado.caso is None:  # pragma: no cover - `liquidaciones` ya lo habría negado
            raise LiquidacionNoDisponibleError(estado.falta, case_id=str(case_id))
        return liquidaciones.actual, estado.caso

    def _intentar_caso(
        self, partidas: Sequence[Partida], detail: CaseDetail
    ) -> tuple[CasoTributario | None, str | None]:
        """El caso que el motor liquida, o el motivo por el que todavía no se puede armar.

        Los tres motivos son distintos y se distinguen a propósito: quedan renglones sin
        decidir (lo normal, y `a_caso` lo dice con el conteo), hay un concepto que el motor
        no modela (hay que llevarlo a mano), o el caso salió con una cifra imposible. El
        tercero NO hace eco del mensaje de pydantic: ese texto habla de campos del modelo y
        quien lee esto es un contador.
        """
        try:
            return (
                a_caso(
                    list(partidas),
                    contribuyente=self._contribuyente(detail),
                    anio_gravable=detail.case.tax_year,
                ),
                None,
            )
        except ValueError as exc:
            return None, str(exc)
        except NotImplementedError as exc:
            return None, str(exc)
        except PydanticValidationError as exc:
            campos = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
            log.warning("conciliacion.caso_invalido", campos=campos)
            return None, (
                "Alguna de las cifras resueltas no puede entrar a la declaración (por "
                "ejemplo un valor negativo). Hay que revisar los renglones resueltos "
                "antes de calcular."
            )

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
        return cargar(detail.case.tax_year)

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
            try:
                resolver(partida, decision, motivo=motivo, quien="sondeo", valor=valor)
            except ValueError:
                continue
            motivos.append(motivo.value)
        if motivos:
            posibles[decision.value] = motivos
    return posibles
