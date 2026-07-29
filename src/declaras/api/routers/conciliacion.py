"""Endpoints del conciliador y de la liquidación: el cable entre las dos mitades.

Todo cuelga de `/v1/cases/{case_id}` porque conciliar es algo que se le hace a UNA
declaración: no hay partidas sin expediente, ni liquidación sin partidas.

EL id DE UN RENGLÓN VA EN LA RUTA Y NO ES UN UUID: es la llave del conciliador
(`900111222:SALARIOS` y sus variantes), derivada del reporte de la DIAN. Lleva dos puntos
sin problema; un nombre de tercero con barra hay que enviarlo codificado (`%2F`), porque
una barra cruda partiría la ruta.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from declaras.api.case_schemas import CaseSummaryResponse
from declaras.api.conciliacion_schemas import (
    CasillaResponse,
    ConciliacionEstadoResponse,
    ConciliacionResumenResponse,
    LiquidacionesResponse,
    PeticionCerradaResponse,
    PeticionResponse,
    RegistrarRespuestaRequest,
    ResolverPartidaRequest,
    ResolverPartidaResponse,
    RespuestaGuardadaResponse,
    RespuestaRegistradaResponse,
)
from declaras.api.deps import ApiKeyDep, ContainerDep
from declaras.services.conciliacion.peticiones import costo_de_cerrar

router = APIRouter(prefix="/v1/cases/{case_id}", tags=["conciliacion"])


@router.post(
    "/conciliacion",
    response_model=ConciliacionResumenResponse,
    summary="Cruza el reporte de terceros con los documentos del cliente y liquida el preliminar",
)
async def conciliar(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> ConciliacionResumenResponse:
    """Idempotente: reconstruye el cruce completo y `refrescar` decide qué resoluciones
    sobreviven. Volver a llamarla preserva las decisiones del contador y repone las
    provisionales del sistema; no duplica nada porque reemplaza en vez de acumular."""
    estado = await container.conciliacion_service.conciliar(case_id)
    return ConciliacionResumenResponse.from_estado(estado)


@router.get(
    "/conciliacion",
    response_model=ConciliacionEstadoResponse,
    summary="Los renglones del cruce, con la plata en juego primero",
)
async def ver_conciliacion(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> ConciliacionEstadoResponse:
    estado = await container.conciliacion_service.estado(case_id)
    return ConciliacionEstadoResponse.from_estado(estado)


@router.post(
    "/conciliacion/{partida_id}/resolver",
    response_model=ResolverPartidaResponse,
    summary="Registra la decisión del contador sobre un renglón y recalcula",
)
async def resolver_partida(
    case_id: UUID,
    partida_id: str,
    payload: ResolverPartidaRequest,
    container: ContainerDep,
    _auth: ApiKeyDep,
) -> ResolverPartidaResponse:
    partida, estado = await container.conciliacion_service.resolver_partida(
        case_id,
        partida_id,
        decision=payload.decision,
        motivo=payload.motivo,
        quien=payload.quien,
        valor=payload.valor,
        nota=payload.nota,
    )
    return ResolverPartidaResponse.from_resultado(partida, estado)


@router.get(
    "/peticiones",
    response_model=list[PeticionResponse],
    summary="Qué documentos hay que pedirle al cliente, priorizados",
)
async def ver_peticiones(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> list[PeticionResponse]:
    peticiones = await container.conciliacion_service.peticiones(case_id)
    return [PeticionResponse.from_peticion(p) for p in peticiones]


@router.get(
    "/respuestas",
    response_model=list[RespuestaGuardadaResponse],
    summary="Lo que el cliente ya contestó, para verlo y poder cambiarlo",
)
async def listar_respuestas(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> list[RespuestaGuardadaResponse]:
    """Una respuesta apaga una pregunta, y apagar una deducción cambia la declaración. Sin poder
    verla, un "no" dado por error no tiene vuelta atrás desde la interfaz."""
    respuestas = await container.conciliacion_service.respuestas(case_id)
    return [
        RespuestaGuardadaResponse.from_respuesta(r)
        for r in sorted(respuestas, key=lambda r: r.cuando, reverse=True)
    ]


@router.post(
    "/respuestas",
    response_model=RespuestaRegistradaResponse,
    summary="Registra lo que contestó el cliente a una pregunta",
)
async def registrar_respuesta(
    case_id: UUID,
    payload: RegistrarRespuestaRequest,
    container: ContainerDep,
    _auth: ApiKeyDep,
) -> RespuestaRegistradaResponse:
    """Un `no` apaga la petición para siempre: sin este registro el sistema le pregunta
    por prepagada al cliente en cada consulta."""
    peticiones = await container.conciliacion_service.registrar_respuesta(
        case_id,
        pregunta=payload.pregunta,
        tiene=payload.tiene,
        detalle=dict(payload.detalle),
        quien=payload.quien,
    )
    return RespuestaRegistradaResponse(
        pregunta=payload.pregunta,
        tiene=payload.tiene,
        peticiones=[PeticionResponse.from_peticion(p) for p in peticiones],
    )


@router.post(
    "/cerrar-peticion/{peticion_id}",
    response_model=PeticionCerradaResponse,
    summary="Cierra una petición sin soporte, diciendo lo que cuesta cerrarla",
)
async def cerrar_peticion(
    case_id: UUID,
    peticion_id: str,
    container: ContainerDep,
    _auth: ApiKeyDep,
    quien: str = "contador",
) -> PeticionCerradaResponse:
    cerrada, quedan = await container.conciliacion_service.cerrar_peticion(
        case_id, peticion_id, quien=quien
    )
    return PeticionCerradaResponse(
        peticion_id=cerrada.id,
        tipo_documento=cerrada.tipo_documento,
        costo=costo_de_cerrar(cerrada),
        costo_es_techo=cerrada.ahorro_es_techo,
        peticiones=[PeticionResponse.from_peticion(p) for p in quedan],
    )


@router.get(
    "/liquidacion",
    response_model=LiquidacionesResponse,
    summary="El 210 preliminar, el de hoy, y la ganancia entre los dos",
)
async def ver_liquidacion(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> LiquidacionesResponse:
    liquidaciones = await container.conciliacion_service.liquidaciones(case_id)
    return LiquidacionesResponse.from_liquidaciones(liquidaciones)


@router.post(
    "/liquidacion/cerrar",
    response_model=CaseSummaryResponse,
    summary="Da el borrador por listo (se niega si hay una alerta bloqueante viva)",
)
async def cerrar_borrador(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> CaseSummaryResponse:
    """La mitad "no permitir cerrar" de que `bloqueante` bloquee de verdad: la declaración
    se puede VER con la alerta impresa (el borrador es donde el contador lee qué le falta),
    pero no darse por buena mientras un ingreso esté por fuera."""
    caso = await container.conciliacion_service.cerrar_borrador(case_id)
    return CaseSummaryResponse(
        id=caso.id,
        client_id=caso.client_id,
        tax_year=caso.tax_year,
        status=caso.status,
        updated_at=caso.updated_at,
    )


@router.get(
    "/formulario",
    response_model=list[CasillaResponse],
    summary="El 210 que se va a radicar, casilla por casilla",
)
async def ver_formulario(
    case_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> list[CasillaResponse]:
    """Las casillas del formulario oficial, con la cifra que se va a declarar en cada una.

    NO es lo mismo que los renglones que la exógena sugiere: eso es lo que la DIAN pondría con lo
    que ella sabe, y esto es lo que queda tras decidir. Medido en un caso real, la misma casilla
    traía cifras con millones de diferencia y nada lo decía.
    """
    casillas = await container.conciliacion_service.formulario(case_id)
    return [CasillaResponse(**vars(c)) for c in casillas]


@router.get(
    "/borrador",
    response_class=Response,
    summary="El borrador del 210, en HTML, con sus alertas",
)
async def ver_borrador(case_id: UUID, container: ContainerDep, _auth: ApiKeyDep) -> Response:
    html = await container.conciliacion_service.borrador(case_id)
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get(
    "/memoria",
    response_class=PlainTextResponse,
    summary="La memoria de cálculo, casilla por casilla, en texto",
)
async def ver_memoria(case_id: UUID, container: ContainerDep, _auth: ApiKeyDep) -> Response:
    texto = await container.conciliacion_service.memoria(case_id)
    return Response(content=texto, media_type="text/markdown; charset=utf-8")
