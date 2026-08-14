"""Contratos HTTP del conciliador y de la liquidación.

Separados de `case_schemas.py` porque son otro dominio: ahí vive el expediente (cliente,
documentos, alertas, bitácora), acá el cruce contra la DIAN y el 210 que sale de él.

Los modelos de entrada son `extra="forbid"`: un typo en el cuerpo (`desicion`) tiene que
reventar en vez de ignorarse y dejar la decisión del contador sin registrar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from declaras.api.case_schemas import CaseDetailResponse
from declaras.motor import Flag, Liquidacion, Nodo
from declaras.parametros.en_palabras import en_palabras
from declaras.render import tipo_de_valor
from declaras.services.conciliacion import (
    ClaseDeIngreso,
    Decision,
    LiquidacionVersionada,
    Motivo,
    Partida,
    Respuesta,
    etiqueta_de_pregunta,
)
from declaras.services.conciliacion.patrimonio import BienCapturado, Valoracion
from declaras.services.conciliacion.peticiones import Peticion
from declaras.services.conciliacion.resolucion import _plata_en_juego
from declaras.services.conciliacion_service import (
    ArchivoIncorporado,
    Estado,
    Liquidaciones,
    VistaPatrimonio,
    clase_sugerida,
    clases_posibles,
    decisiones_posibles,
)


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─────────────────────────── entradas ───────────────────────────


class ResolverPartidaRequest(_Entrada):
    """La decisión del contador sobre un renglón del cruce.

    `valor` lo aceptan `USAR_OTRO` y `CLASIFICAR`; las demás decisiones toman la cifra de la
    versión que escogen, y un valor ignorado en silencio sería una decisión del contador que se
    pierde (el conciliador lo rechaza, no lo descarta).

    `clase` SOLO la acepta `CLASIFICAR`, y ahí es obligatoria: dice a qué cédula del 210 va el
    ingreso, y eso cambia el impuesto.
    """

    decision: Decision
    motivo: Motivo
    clase: ClaseDeIngreso | None = None
    valor: int | None = Field(default=None, ge=0)
    nota: str | None = None
    # YA NO SE LEE. El actor de una decisión sale de la credencial verificada, en
    # `resolver_partida`. Se conserva el campo para no romper a un cliente que todavía lo manda
    # —el esquema rechazaría la petición con `extra="forbid"`— pero lo que llegue acá se ignora.
    # Se borra cuando el front deje de mandarlo.
    quien: str = Field(default="contador", min_length=1, max_length=200)


class RegistrarRespuestaRequest(_Entrada):
    """Lo que el cliente contestó a una pregunta. Un `no` apaga la petición para siempre."""

    pregunta: str = Field(min_length=1, max_length=300)
    tiene: bool
    detalle: dict[str, Any] = Field(default_factory=dict)
    quien: str = Field(default="cliente", min_length=1, max_length=200)


class GuardarBienRequest(_Entrada):
    """Un bien del patrimonio: la casa, el carro, la moto.

    Los insumos van CRUDOS y separados en vez de un `valor` ya resuelto, porque la regla de
    valoración depende del tipo y hay que poder explicarla después. Para un inmueble el art. 277
    pide el MAYOR entre el costo de adquisición y el avalúo del predial, así que mandar uno solo
    da una cifra que puede quedarse corta; para un vehículo el art. 267 pide el costo, y el avalúo
    del impuesto vehicular —que es el papel que la gente tiene a mano— no sirve para eso.

    `id` lo pone el cliente para que corregir un bien sea la misma operación que crearlo: el caso
    normal de esta pantalla es capturar el inmueble sin el predial y completarlo cuando llega, y
    con solo alta y baja cada corrección dejaría un duplicado.
    """

    id: str = Field(min_length=1, max_length=36)
    tipo: Literal["inmueble", "vehiculo", "otro"]
    descripcion: str = Field(min_length=1, max_length=200)
    identificacion: str | None = Field(default=None, max_length=100)
    costo_adquisicion: int | None = Field(default=None, ge=0)
    avaluo_catastral: int | None = Field(default=None, ge=0)
    valor_declarado: int | None = Field(default=None, ge=0)
    deuda_saldo: int | None = Field(default=None, ge=0)
    deuda_acreedor: str | None = Field(default=None, max_length=200)
    cilindraje: int | None = Field(default=None, ge=0)
    quien: str = Field(default="cliente", min_length=1, max_length=200)


# ─────────────────────────── salidas ───────────────────────────


class FlagResponse(BaseModel):
    codigo: str
    mensaje: str
    severidad: str

    @classmethod
    def from_flag(cls, flag: Flag) -> FlagResponse:
        return cls(codigo=flag.codigo, mensaje=flag.mensaje, severidad=flag.severidad)


class ValorResponse(BaseModel):
    """Lo que un lado afirma. `retencion` es `None` cuando ese lado NO la reportó, que no
    es lo mismo que reportar cero: el XLSX real de la exógena no trae esa columna."""

    monto: int
    retencion: int | None
    lado: str
    tercero: str | None
    celda: str | None
    confianza: float | None


class ResolucionResponse(BaseModel):
    decision: Decision
    valor: int
    motivo: Motivo
    clase: ClaseDeIngreso | None
    origen: str
    nota: str | None
    quien: str
    cuando: datetime


class PartidaResponse(BaseModel):
    """Un renglón del cruce como lo pinta la consola del contador.

    Las cuatro marcas estructurales del cruce viajan enteras (`reportado_a`,
    `documentos_por_cruzar`, `version_que_rige`, `versiones_documento` por su conteo): son
    lo que distingue "esta plata es de otra persona" de una nota de texto, y una interfaz
    que no las vea vuelve a meter el ingreso de un tercero al caso del contribuyente.
    """

    id: str
    nit_tercero: str
    nombre_tercero: str
    concepto: str | None
    codigos_crudos: list[str]
    estado: str
    nota: str | None
    reportado_a: str | None
    documentos_por_cruzar: list[str]
    version_que_rige: str | None
    versiones_documento: int
    version_dian: ValorResponse | None
    version_documento: ValorResponse | None
    diferencia_monto: int
    diferencia_retencion: int
    plata_en_juego: int
    resolucion: ResolucionResponse | None
    # Qué puede decidir el contador sobre este renglón, y con qué motivo cada cosa. Se
    # deriva preguntándole al conciliador, no copiando su tabla: la interfaz no puede
    # ofrecer una decisión que el backend rechaza, ni esconder la única salida que un
    # renglón tiene (p. ej. LLEVAR_A_MANO, que solo aplica a los conceptos que el motor
    # todavía no liquida).
    decisiones_posibles: dict[str, list[str]]
    # Qué clase de ingreso sostiene cada motivo de `CLASIFICAR`. Vacío en los renglones que el
    # motor ya sabe ubicar, que son casi todos.
    clases_posibles: dict[str, list[str]]
    # La clase que el sistema sugiere, cuando el concepto de la exógena la implica. Es una
    # sugerencia, no una decisión: quien resuelve tiene que confirmar el hecho del que depende.
    clase_sugerida: str | None

    @classmethod
    def from_partida(cls, partida: Partida) -> PartidaResponse:
        return cls(
            id=partida.id,
            nit_tercero=partida.nit_tercero,
            nombre_tercero=partida.nombre_tercero,
            concepto=partida.concepto.value if partida.concepto is not None else None,
            codigos_crudos=list(partida.codigos_crudos),
            estado=partida.estado.value,
            nota=partida.nota,
            reportado_a=partida.reportado_a,
            documentos_por_cruzar=list(partida.documentos_por_cruzar),
            version_que_rige=partida.version_que_rige,
            versiones_documento=len(partida.versiones_documento),
            version_dian=_valor(partida.version_dian),
            version_documento=_valor(partida.version_documento),
            diferencia_monto=partida.diferencia_monto,
            diferencia_retencion=partida.diferencia_retencion,
            plata_en_juego=_plata_en_juego(partida),
            resolucion=_resolucion(partida),
            decisiones_posibles=decisiones_posibles(partida),
            clases_posibles=clases_posibles(partida),
            clase_sugerida=clase_sugerida(partida),
        )


def _valor(valor: Any) -> ValorResponse | None:
    if valor is None:
        return None
    return ValorResponse(
        monto=valor.monto,
        retencion=valor.retencion,
        lado=valor.lado.value,
        tercero=valor.tercero,
        celda=valor.celda,
        confianza=valor.confianza,
    )


def _resolucion(partida: Partida) -> ResolucionResponse | None:
    r = partida.resolucion
    if r is None:
        return None
    return ResolucionResponse(
        decision=r.decision,
        valor=r.valor,
        motivo=r.motivo,
        clase=r.clase,
        origen=r.origen.value,
        nota=r.nota,
        quien=r.quien,
        cuando=r.cuando,
    )


class ConciliacionResumenResponse(BaseModel):
    """El resultado de conciliar: cuánto hay, cuánto falta decidir y qué se perdió de vista."""

    total: int
    pendientes: int
    por_estado: dict[str, int]
    # Las huérfanas de `refrescar`: decisiones cuya partida desapareció de la re-derivación
    # (la DIAN republicó el reporte sin esa fila). Se listan en vez de botarse — botarlas
    # escondería la decisión de una persona y la deducción que la sostenía.
    resoluciones_sin_partida: list[PartidaResponse]
    # None cuando el 210 sí se pudo calcular; si no, QUÉ falta, en palabras accionables.
    falta_para_liquidar: str | None

    @classmethod
    def from_estado(cls, estado: Estado) -> ConciliacionResumenResponse:
        return cls(
            total=len(estado.partidas),
            pendientes=len(estado.pendientes),
            por_estado=estado.por_estado,
            resoluciones_sin_partida=[PartidaResponse.from_partida(p) for p in estado.huerfanas],
            falta_para_liquidar=estado.falta,
        )


class ConciliacionEstadoResponse(BaseModel):
    """Los renglones del cruce, con la plata en juego primero."""

    partidas: list[PartidaResponse]
    pendientes: list[str]
    resoluciones_sin_partida: list[PartidaResponse]
    falta_para_liquidar: str | None

    @classmethod
    def from_estado(cls, estado: Estado) -> ConciliacionEstadoResponse:
        return cls(
            partidas=[PartidaResponse.from_partida(p) for p in estado.ordenadas],
            pendientes=[p.id for p in estado.pendientes],
            resoluciones_sin_partida=[PartidaResponse.from_partida(p) for p in estado.huerfanas],
            falta_para_liquidar=estado.falta,
        )


class ResolverPartidaResponse(BaseModel):
    partida: PartidaResponse
    resumen: ConciliacionResumenResponse

    @classmethod
    def from_resultado(cls, partida: Partida, estado: Estado) -> ResolverPartidaResponse:
        return cls(
            partida=PartidaResponse.from_partida(partida),
            resumen=ConciliacionResumenResponse.from_estado(estado),
        )


class PeticionResponse(BaseModel):
    """Un documento (o una pregunta) que le falta al expediente.

    `ahorro_es_techo` NO es decorativo: distingue una cifra medida (los aportes
    obligatorios son tarifas de ley sobre un pago ya reportado) del tope legal de un
    beneficio, que depende de cuánto pagó el cliente. La interfaz escribe "hasta $X"
    cuando es `true`; presentar los dos números igual sería prometerle al cliente una
    cifra que nadie sostiene.
    """

    id: str
    tipo_documento: str
    tercero: dict[str, str] | None
    razon: str
    ahorro_estimado: int
    ahorro_es_techo: bool
    # Por qué el ahorro es el que es, cuando no es una medición limpia. Sin esto, "no baja nada",
    # "no se puede calcular todavía" y "es el techo legal" se ven iguales en pantalla, y las tres
    # llevan a decisiones distintas.
    ahorro_por_que: str | None = None
    prioridad: int
    pregunta_previa: str | None
    copy_sugerido: str

    @classmethod
    def from_peticion(cls, peticion: Peticion) -> PeticionResponse:
        return cls(**peticion.model_dump())


class RespuestaRegistradaResponse(BaseModel):
    pregunta: str
    tiene: bool
    peticiones: list[PeticionResponse]


class PeticionCerradaResponse(BaseModel):
    """Lo que cuesta cerrar una petición sin soporte: el ahorro que se deja de tener."""

    peticion_id: str
    tipo_documento: str
    costo: int
    costo_es_techo: bool
    peticiones: list[PeticionResponse]


class LiquidacionResponse(BaseModel):
    """Una versión de la liquidación, con lo que hay que mirar antes de presentarla."""

    version: int
    momento: datetime
    impuesto: int
    saldo: int
    flags: list[FlagResponse]
    # Si hay uno solo de estos, la declaración se puede ver pero no darse por lista.
    bloqueantes: list[FlagResponse]
    casillas: list[NodoResponse]

    @classmethod
    def from_version(cls, version: LiquidacionVersionada) -> LiquidacionResponse:
        liq = version.liquidacion
        return cls(
            version=version.version,
            momento=version.momento,
            impuesto=version.impuesto,
            saldo=version.saldo,
            flags=[FlagResponse.from_flag(f) for f in liq.flags],
            bloqueantes=[
                FlagResponse.from_flag(f) for f in liq.flags if f.severidad == "bloqueante"
            ],
            casillas=_casillas(liq),
        )


class NodoResponse(BaseModel):
    """Una casilla del borrador con su fórmula: la trazabilidad que el contador audita.

    Lleva DOS nombres del mismo paso. `etiqueta` es el del contador ("INCRNGO aportes obligatorios
    salud/pensión"), que es el correcto y el que va en la memoria que se anexa; `en_palabras` es el
    mismo paso dicho para quien declara una vez al año. Quien pinta elige según a quién le habla, y
    traducir destruyendo el original dejaría al contador sin el nombre con que defiende la cifra.
    """

    codigo: str
    etiqueta: str
    en_palabras: str
    # `si_no` o `pesos`: OBLIGADO_DECLARAR vale 1 y no es un peso.
    tipo: str
    valor: int
    formula: str
    insumos: list[str]
    regla: str | None

    @classmethod
    def from_nodo(cls, n: Nodo) -> NodoResponse:
        return cls(
            codigo=n.codigo,
            etiqueta=n.etiqueta,
            en_palabras=en_palabras(n.codigo, n.etiqueta),
            tipo=tipo_de_valor(n.codigo),
            valor=n.valor,
            formula=n.formula,
            insumos=list(n.insumos),
            regla=n.regla,
        )


def _casillas(liquidacion: Liquidacion) -> list[NodoResponse]:
    # Se recorre el orden del borrador y no `liq.nodos`: el diccionario lleva el orden de
    # cálculo, y una lista de casillas en ese orden no es la que un contador lee.
    from declaras.render import ORDEN_CASILLAS

    return [
        NodoResponse.from_nodo(n)
        for codigo in ORDEN_CASILLAS
        if (n := liquidacion.nodos.get(codigo)) is not None
    ]


class LiquidacionesResponse(BaseModel):
    """El preliminar, la de hoy, y lo que el trabajo con los documentos le ahorró.

    `ganancia` puede ser NEGATIVA y eso no se esconde: un certificado que muestra un
    ingreso que la exógena no tenía sube el impuesto, y taparlo sería mentirle al cliente
    sobre lo que va a pagar.
    """

    preliminar: LiquidacionResponse
    actual: LiquidacionResponse
    ganancia: int
    ganancia_saldo: int
    # ¿`actual` corresponde a los renglones que hay hoy? False cuando llegó un documento y
    # quedan decisiones pendientes: entonces `actual` es lo último que se pudo calcular y
    # NO la declaración de hoy, y las dos ganancias se miden contra ella. Sin esta marca la
    # interfaz pinta la cifra de antes del último documento como si fuera la vigente.
    actual_vigente: bool
    # Qué falta para que vuelva a haber una liquidación de hoy (None si está vigente).
    falta_para_liquidar: str | None
    # ¿El preliminar se liquidó SIN ningún documento del cliente? Es lo que hace que la
    # ganancia signifique "lo que el trabajo con los documentos le ahorró". False cuando el
    # expediente ya tenía documentos cruzables antes de la primera conciliación y el
    # preliminar puro no se pudo armar: ahí la ganancia subestima, y hay que decirlo.
    preliminar_sin_documentos: bool

    @classmethod
    def from_liquidaciones(cls, liquidaciones: Liquidaciones) -> LiquidacionesResponse:
        return cls(
            preliminar=LiquidacionResponse.from_version(liquidaciones.preliminar),
            actual=LiquidacionResponse.from_version(liquidaciones.actual),
            actual_vigente=liquidaciones.vigente,
            falta_para_liquidar=liquidaciones.falta,
            preliminar_sin_documentos=liquidaciones.preliminar.base_sin_documentos,
            ganancia=liquidaciones.ganancia,
            # Lo que el cliente SIENTE: la retención no baja el impuesto, baja lo que le
            # toca girar. La ganancia del contrato es la del impuesto; esta va al lado.
            ganancia_saldo=liquidaciones.preliminar.saldo - liquidaciones.actual.saldo,
        )


class ArchivoIncorporadoResponse(BaseModel):
    """El desenlace de UN archivo de una subida masiva, derivado del cruce real."""

    archivo: str
    doc_type: str
    estado: str
    peticion_cerrada: bool | None
    motivo: str | None

    @classmethod
    def from_resultado(cls, resultado: ArchivoIncorporado) -> ArchivoIncorporadoResponse:
        return cls(
            archivo=resultado.archivo,
            doc_type=resultado.doc_type,
            estado=resultado.estado,
            peticion_cerrada=resultado.peticion_cerrada,
            motivo=resultado.motivo,
        )


class UploadDocumentsResponse(CaseDetailResponse):
    """El expediente completo MÁS el desenlace de cada archivo que acabó de entrar.

    Extiende la respuesta que ya devolvía la subida en vez de reemplazarla: la consola lee
    `documents` para pintar la lista, y cambiarle la forma por debajo la rompería. Lo nuevo
    es `resultados`, que es lo que la pantalla de peticiones necesita para decir "este
    empareja y abre una discrepancia" en vez de "listo".
    """

    resultados: list[ArchivoIncorporadoResponse]


class RespuestaGuardadaResponse(BaseModel):
    """Una pregunta ya contestada, para poder verla y cambiarla.

    Existe porque contestar apagaba la pregunta sin dejar nada a la vista: un "no" dado por
    error era irrecuperable desde la interfaz, y quien revisara después no podía distinguir una
    deducción que falta porque nadie preguntó de una que falta porque el cliente dijo que no la
    tenía. Son dos situaciones distintas y llevan a decisiones distintas.
    """

    pregunta: str
    etiqueta: str
    tiene: bool
    quien: str
    cuando: datetime

    @classmethod
    def from_respuesta(cls, r: Respuesta) -> RespuestaGuardadaResponse:
        return cls(
            pregunta=r.pregunta,
            etiqueta=etiqueta_de_pregunta(r.pregunta),
            tiene=r.tiene,
            quien=r.quien,
            cuando=r.cuando,
        )


class CasillaResponse(BaseModel):
    """Una casilla del formulario 210, con la cifra que se va a declarar.

    `nodo` permite volver del formulario a la memoria de cálculo: de "la casilla 97 dice
    $62.800.000" a "y sale de RLG_GENERAL, art. 336 ET".
    """

    numero: int
    nombre: str
    valor: int
    nodo: str | None = None


class BienResponse(BaseModel):
    """Un bien capturado con su valor y, sobre todo, con la regla que produjo ese valor.

    `regla` no es adorno, es lo que permite defender la cifra. "Es el avalúo del predial porque era
    mayor que los $80.000.000 de la escritura" se sostiene ante la DIAN; "$120.000.000" no.
    """

    id: str
    tipo: str
    descripcion: str
    identificacion: str | None
    costo_adquisicion: int | None
    avaluo_catastral: int | None
    valor_declarado: int | None
    deuda_saldo: int | None
    deuda_acreedor: str | None
    cilindraje: int | None
    valor: int
    regla: str
    # El artículo, aparte de la frase. Al contador le dice todo y al titular no le dice nada, así
    # que la pantalla decide a quién se lo muestra en vez de tener que elegir un solo texto.
    norma: str | None
    # Qué falta para poder sostener la cifra. `None` es "está completo".
    falta: str | None

    @classmethod
    def from_bien(cls, bien: BienCapturado, valoracion: Valoracion) -> BienResponse:
        return cls(
            **bien.model_dump(include=set(BienCapturado.model_fields) - {"quien", "cuando"}),
            valor=valoracion.valor,
            regla=valoracion.regla,
            norma=valoracion.norma,
            falta=valoracion.falta,
        )


class PreguntaPatrimonioResponse(BaseModel):
    """Una compuerta del cuestionario, con lo que se contestó si ya se contestó."""

    pregunta: str
    tipo: str
    texto: str
    texto_contador: str
    por_que: str
    documento: str
    copy_sugerido: str
    # `None` es "sin contestar", que no es lo mismo que "contestó que no". La diferencia decide si
    # se le vuelve a preguntar al cliente.
    contestada: bool | None


class ActivoReportadoResponse(BaseModel):
    """Un activo que llegó solo, por el reporte de un tercero. No hay nada que preguntar de él."""

    descripcion: str
    valor: int


class PatrimonioResponse(BaseModel):
    """El patrimonio del caso completo, para poder preguntar sin preguntar de más.

    Trae las dos mitades juntas porque la pantalla las necesita juntas: quien va a pedirle un papel
    a un cliente tiene que ver primero lo que el sistema ya sabe, o la pregunta más cara del
    cuestionario es la que se hace por algo que ya estaba contado.
    """

    preguntas: list[PreguntaPatrimonioResponse]
    bienes: list[BienResponse]
    reportados: list[ActivoReportadoResponse]
    deudas_reportadas: list[ActivoReportadoResponse]
    # Lo capturado y lo reportado, separados y sumados. El bruto es la casilla 29.
    total_capturado: int
    total_reportado: int
    total_bruto: int
    total_deudas: int
    patrimonio_liquido_anterior: int | None
    falta: list[str]
    completo: bool

    @classmethod
    def from_vista(cls, vista: VistaPatrimonio) -> PatrimonioResponse:
        reportado = sum(a.valor_31dic for a in vista.reportados)
        return cls(
            preguntas=[
                PreguntaPatrimonioResponse(
                    **p.model_dump(), contestada=vista.contestadas.get(p.pregunta)
                )
                for p in vista.preguntas
            ],
            bienes=[BienResponse.from_bien(b, v) for b, v in vista.bienes],
            reportados=[
                ActivoReportadoResponse(descripcion=a.descripcion, valor=a.valor_31dic)
                for a in vista.reportados
            ],
            deudas_reportadas=[
                ActivoReportadoResponse(descripcion=d.acreedor, valor=d.saldo_31dic)
                for d in vista.deudas_reportadas
            ],
            total_capturado=vista.capturado,
            total_reportado=reportado,
            total_bruto=vista.capturado + reportado,
            total_deudas=sum(d.saldo_31dic for d in vista.deudas_reportadas)
            + sum(b.deuda_saldo or 0 for b, _ in vista.bienes),
            patrimonio_liquido_anterior=vista.patrimonio_liquido_anterior,
            falta=vista.falta,
            completo=vista.completo,
        )
