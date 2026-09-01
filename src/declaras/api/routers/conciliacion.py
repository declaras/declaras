"""Endpoints del conciliador y de la liquidación: el cable entre las dos mitades.

Todo cuelga de `/v1/cases/{case_id}` porque conciliar es algo que se le hace a UNA
declaración: no hay partidas sin expediente, ni liquidación sin partidas.

EL id DE UN RENGLÓN VA EN LA RUTA Y NO ES UN UUID: es la llave del conciliador
(`900111222:SALARIOS` y sus variantes), derivada del reporte de la DIAN. Lleva dos puntos
sin problema; un nombre de tercero con barra hay que enviarlo codificado (`%2F`), porque
una barra cruda partiría la ruta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from declaras.api.case_schemas import CaseSummaryResponse
from declaras.api.conciliacion_schemas import (
    CasillaResponse,
    ConciliacionEstadoResponse,
    ConciliacionResumenResponse,
    GuardarBienRequest,
    LiquidacionesResponse,
    PatrimonioResponse,
    PeticionCerradaResponse,
    PeticionResponse,
    RegistrarRespuestaRequest,
    ResolverPartidaRequest,
    ResolverPartidaResponse,
    RespuestaGuardadaResponse,
    RespuestaRegistradaResponse,
)
from declaras.api.deps import AutenticadoDep, ContainerDep
from declaras.services.comparacion_210 import Comparacion210
from declaras.services.conciliacion.patrimonio import BienCapturado
from declaras.services.conciliacion.peticiones import costo_de_cerrar
from declaras.services.conciliacion.recomendaciones import Recomendaciones

router = APIRouter(prefix="/v1/cases/{case_id}", tags=["conciliacion"])


@router.post(
    "/conciliacion",
    response_model=ConciliacionResumenResponse,
    summary="Cruza el reporte de terceros con los documentos del cliente y liquida el preliminar",
)
async def conciliar(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
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
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
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
    quien: AutenticadoDep,
) -> ResolverPartidaResponse:
    partida, estado = await container.conciliacion_service.resolver_partida(
        case_id,
        partida_id,
        decision=payload.decision,
        motivo=payload.motivo,
        # EL ACTOR SALE DE LA CREDENCIAL VERIFICADA, NO DEL CUERPO DE LA PETICION.
        #
        # Antes venia en `payload.quien`, o sea que el navegador DECLARABA quien habia decidido —y
        # la consola mandaba "contador" fijo, sin importar quien estuviera del otro lado. Eso no es
        # un rastro de auditoria: es una etiqueta que el cliente elige, y en un expediente
        # tributario el rastro es medio punto de la razon por la que existe.
        #
        # El campo del esquema se conserva por compatibilidad pero ya no se lee: ver
        # `ResolverPartidaRequest.quien`.
        quien=quien.para_bitacora,
        valor=payload.valor,
        clase=payload.clase,
        nota=payload.nota,
    )
    return ResolverPartidaResponse.from_resultado(partida, estado)


@router.get(
    "/peticiones",
    response_model=list[PeticionResponse],
    summary="Qué documentos hay que pedirle al cliente, priorizados",
)
async def ver_peticiones(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> list[PeticionResponse]:
    peticiones = await container.conciliacion_service.peticiones(case_id)
    return [PeticionResponse.from_peticion(p) for p in peticiones]


@router.get(
    "/recomendaciones",
    response_model=Recomendaciones,
    summary="Cuánto impuesto le ahorraría cada beneficio, esté o no pedido",
)
async def ver_recomendaciones(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> Recomendaciones:
    """El catálogo completo de beneficios con lo que cada uno baja el impuesto, en pesos.

    A diferencia de `/peticiones`, no descarta lo que ya se contestó: un "no tengo prepagada"
    apagaba la petición y con ella la única cifra que decía cuánta plata se estaba dejando en la
    mesa. Aquí el beneficio descartado sigue apareciendo, con lo que habría ahorrado.
    """
    recomendaciones: Recomendaciones = await container.conciliacion_service.recomendaciones(case_id)
    return recomendaciones


@router.get(
    "/comparacion-con-la-dian",
    response_model=Comparacion210,
    summary="El borrador que la DIAN precargó contra el nuestro, casilla por casilla",
)
async def ver_comparacion_con_la_dian(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> Comparacion210:
    """Dónde difiere lo que se va a radicar de lo que la DIAN precargó.

    La DIAN precrea un borrador con lo que los terceros le reportaron y se puede firmar tal cual.
    Se ve oficial pero es una sugerencia, y su propia documentación lo dice. Las diferencias son el
    valor del trabajo con documentos; y una casilla nuestra MENOR que la suya sin razón registrada
    es un ingreso que se perdió, que es lo que la DIAN cruza sola.
    """
    comparacion: Comparacion210 = await container.conciliacion_service.comparacion_con_la_dian(
        case_id
    )
    return comparacion


@router.get(
    "/comparacion-con-lo-presentado",
    response_model=Comparacion210,
    summary="El cálculo contra la declaración que de verdad se presentó ese año",
)
async def ver_comparacion_con_lo_presentado(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> Comparacion210:
    """La segunda opinión sobre un año ya declarado.

    Lo presentado en un año viejo es casi siempre el trabajo de un contador. Cada diferencia es un
    beneficio que él no tomó (plata que el cliente dejó sobre la mesa) o un error nuestro, y las dos
    lecturas importan. En el año en curso sale no disponible porque todavía no hay nada presentado.
    """
    comparacion: Comparacion210 = (
        await container.conciliacion_service.comparacion_con_lo_presentado(case_id)
    )
    return comparacion


@router.get(
    "/patrimonio",
    response_model=PatrimonioResponse,
    summary="El patrimonio del caso: lo que ya se sabe y lo que falta preguntar",
)
async def ver_patrimonio(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> PatrimonioResponse:
    """Las dos mitades del patrimonio en una sola lectura.

    Una llega sola (los saldos bancarios y las cesantías vienen en la exógena) y la otra hay que
    preguntarla, porque ninguna notaría le reporta a la DIAN, año tras año, que alguien SIGUE
    siendo dueño de su apartamento. Van juntas para que nadie le pida a un cliente el papel de
    algo que el sistema ya tenía contado.
    """
    return PatrimonioResponse.from_vista(await container.conciliacion_service.patrimonio(case_id))


@router.post(
    "/patrimonio/bienes",
    response_model=PatrimonioResponse,
    summary="Agrega o corrige un bien del patrimonio",
)
async def guardar_bien(
    case_id: UUID,
    payload: GuardarBienRequest,
    container: ContainerDep,
    _auth: AutenticadoDep,
) -> PatrimonioResponse:
    """Un bien capturado cambia la casilla 29, y con ella puede cambiar si la persona está
    obligada a declarar: el patrimonio bruto es uno de los cinco topes del art. 592."""
    bien = BienCapturado(**payload.model_dump(), cuando=datetime.now(tz=UTC))
    return PatrimonioResponse.from_vista(
        await container.conciliacion_service.guardar_bien(case_id, bien)
    )


@router.delete(
    "/patrimonio/bienes/{bien_id}",
    response_model=PatrimonioResponse,
    summary="Quita un bien del patrimonio",
)
async def borrar_bien(
    case_id: UUID, bien_id: str, container: ContainerDep, _auth: AutenticadoDep
) -> PatrimonioResponse:
    return PatrimonioResponse.from_vista(
        await container.conciliacion_service.borrar_bien(case_id, bien_id)
    )


@router.get(
    "/respuestas",
    response_model=list[RespuestaGuardadaResponse],
    summary="Lo que el cliente ya contestó, para verlo y poder cambiarlo",
)
async def listar_respuestas(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
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
    _auth: AutenticadoDep,
) -> RespuestaRegistradaResponse:
    """Un `no` apaga la petición para siempre: sin este registro el sistema le pregunta
    por prepagada al cliente en cada consulta.

    ACÁ `quien` SÍ VIENE DEL CUERPO, Y NO ES EL MISMO CASO QUE EN `resolver_partida`.

    Allá `quien` es el ACTOR —alguien tomó una decisión y hay que saber quién— y por eso tiene que
    salir de la credencial. Acá es la FUENTE del dato: se está registrando lo que contestó el
    cliente, que no es quien opera la consola. Reemplazarlo por el usuario autenticado no
    corregiría una mentira, borraría una distinción real: dejaría de constar que el hecho lo
    afirmó el cliente y no el contador.
    """
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


@router.delete(
    "/respuestas/{pregunta}",
    response_model=RespuestaRegistradaResponse,
    summary="Deshace una respuesta: la pregunta vuelve a estar sin contestar",
)
async def deshacer_respuesta(
    case_id: UUID,
    pregunta: str,
    container: ContainerDep,
    auth: AutenticadoDep,
) -> RespuestaRegistradaResponse:
    """Para cuando se contestó por error, que es más frecuente de lo que parece.

    ═══ POR QUE NO ALCANZA CON CONTESTAR AL REVES ═══

    "Sin contestar" y "contestó que no" son estados distintos: el primero deja la pregunta
    viva, el segundo la apaga para siempre. Deshacer un "no" escribiendo un "sí" no devuelve
    las cosas a como estaban, y encima afirma en nombre del cliente algo que nunca dijo.

    ACA `quien` SALE DE LA CREDENCIAL y no del cuerpo, al reves que al registrar. Es la misma
    distinción de siempre: registrar una respuesta guarda lo que dijo el CLIENTE (la fuente del
    dato), y deshacerla es una acción de quien opera la consola (el actor). Poner al cliente
    como autor de un deshacer que él no pidió sería una mentira en la bitácora.
    """
    peticiones = await container.conciliacion_service.deshacer_respuesta(
        # El correo puede faltar (un servicio no tiene): se cae en el `subject`, que siempre
        # esta, en vez de dejar la bitacora diciendo "lo hizo None".
        case_id,
        pregunta=pregunta,
        quien=auth.email or auth.subject,
    )
    return RespuestaRegistradaResponse(
        pregunta=pregunta,
        # Sin respuesta no hay `tiene`: es justamente el estado al que se vuelve.
        tiene=None,
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
    quien: AutenticadoDep,
) -> PeticionCerradaResponse:
    # Venia como parametro de query con default "contador": cualquiera podia firmar el cierre con
    # el nombre que quisiera, y sin poner nada quedaba firmado como el contador. Ahora sale de la
    # credencial, igual que en `resolver_partida`.
    cerrada, quedan = await container.conciliacion_service.cerrar_peticion(
        case_id, peticion_id, quien=quien.para_bitacora
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
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
) -> LiquidacionesResponse:
    liquidaciones = await container.conciliacion_service.liquidaciones(case_id)
    return LiquidacionesResponse.from_liquidaciones(liquidaciones)


@router.post(
    "/liquidacion/cerrar",
    response_model=CaseSummaryResponse,
    summary="Da el borrador por listo (se niega si hay una alerta bloqueante viva)",
)
async def cerrar_borrador(
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
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
    case_id: UUID, container: ContainerDep, _auth: AutenticadoDep
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
async def ver_borrador(case_id: UUID, container: ContainerDep, _auth: AutenticadoDep) -> Response:
    html = await container.conciliacion_service.borrador(case_id)
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get(
    "/memoria",
    response_class=PlainTextResponse,
    summary="La memoria de cálculo, casilla por casilla, en texto",
)
async def ver_memoria(case_id: UUID, container: ContainerDep, _auth: AutenticadoDep) -> Response:
    texto = await container.conciliacion_service.memoria(case_id)
    return Response(content=texto, media_type="text/markdown; charset=utf-8")
