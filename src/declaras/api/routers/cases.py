"""Endpoints del expediente: cliente, caso, documentos, flags y bitacora.

Es la API que consume la consola del contador y el agente conversacional para vincular
lo que el conector DIAN descarga o lo que el cliente sube por chat a un expediente.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile

from declaras.api.case_schemas import (
    CaseDetailResponse,
    CaseFlagResponse,
    CaseSummaryResponse,
    ClientResponse,
    LinkExtractionRequest,
    OpenCaseRequest,
    ResolveFlagRequest,
)
from declaras.api.conciliacion_schemas import (
    ArchivoIncorporadoResponse,
    UploadDocumentsResponse,
)
from declaras.api.deps import ApiKeyDep, ContainerDep
from declaras.domain.errors import (
    ConflictoDeConcurrenciaError,
    DeclarasError,
    JobNotFoundError,
    ValidationError,
)
from declaras.observability import get_logger
from declaras.services.case_summary import CaseSummary, build_summary
from declaras.services.conciliacion_service import (
    ESTADO_A_BANDEJA,
    ESTADO_NO_RECIBIDO,
    ArchivoIncorporado,
    Subido,
)
from declaras.services.conciliacion_service import (
    MOTIVO_CRUCE_NO_CORRIO as _MOTIVO_CRUCE_NO_CORRIO,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["cases"])


def _download_url(storage_uri: str, filename: str | None = None) -> str:
    """URL de entrega de un documento, con su nombre real en la ruta.

    El nombre va en la ruta y no en un parametro porque el visor de PDF del navegador titula el
    documento con el ultimo segmento de la URL.
    """
    ruta = "/v1/documents/content"
    if filename:
        ruta += f"/{quote(filename, safe='')}"
    return f"{ruta}?uri={quote(storage_uri, safe='')}"


@router.post(
    "/cases",
    response_model=CaseDetailResponse,
    status_code=201,
    summary="Abre un expediente para un cliente y un anio gravable",
)
async def open_case(
    payload: OpenCaseRequest, container: ContainerDep, _auth: ApiKeyDep
) -> CaseDetailResponse:
    detail = await container.case_service.open_case(
        id_kind=payload.id_kind,
        id_number=payload.id_number,
        tax_year=payload.tax_year,
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        email=payload.email,
    )
    return CaseDetailResponse.from_domain(detail, download_url_builder=_download_url)


@router.get(
    "/cases",
    response_model=list[CaseSummaryResponse],
    summary="Lista los expedientes (para la consola del contador)",
)
async def list_cases(
    container: ContainerDep, _auth: ApiKeyDep, limit: int = 50, offset: int = 0
) -> list[CaseSummaryResponse]:
    cases = await container.cases.list_all(limit=limit, offset=offset)
    return [
        CaseSummaryResponse(
            id=c.id,
            client_id=c.client_id,
            tax_year=c.tax_year,
            status=c.status,
            updated_at=c.updated_at,
        )
        for c in cases
    ]


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetailResponse,
    summary="El expediente completo: cliente, documentos, flags y bitacora",
)
async def get_case(case_id: UUID, container: ContainerDep, _auth: ApiKeyDep) -> CaseDetailResponse:
    detail = await container.case_service.get_detail(case_id)
    return CaseDetailResponse.from_domain(detail, download_url_builder=_download_url)


@router.get(
    "/cases/{case_id}/summary",
    response_model=CaseSummary,
    summary="Resumen de lo que el sistema ya sabe del expediente",
)
async def get_case_summary(case_id: UUID, container: ContainerDep, _auth: ApiKeyDep) -> CaseSummary:
    detail = await container.case_service.get_detail(case_id)
    return build_summary(detail)


@router.post(
    "/cases/{case_id}/link-extraction",
    response_model=CaseDetailResponse,
    summary="Vuelca al expediente el resultado de una extraccion DIAN ya terminada",
)
async def link_extraction(
    case_id: UUID, payload: LinkExtractionRequest, container: ContainerDep, _auth: ApiKeyDep
) -> CaseDetailResponse:
    job = await container.jobs.get(payload.job_id)
    if job is None:
        raise JobNotFoundError(job_id=str(payload.job_id))
    detail = await container.case_service.link_extraction_result(
        case_id=case_id, extraction_job=job
    )
    return CaseDetailResponse.from_domain(detail, download_url_builder=_download_url)


@router.post(
    "/cases/{case_id}/documents",
    response_model=UploadDocumentsResponse,
    summary="El cliente sube uno o varios documentos (fotos o PDF)",
)
async def upload_client_document(
    case_id: UUID,
    container: ContainerDep,
    _auth: ApiKeyDep,
    doc_type: list[str] = Form(
        ..., description="Tipo de cada documento, en el mismo orden que los archivos"
    ),
    file: list[UploadFile] = File(..., description="Uno o varios archivos"),
    peticion_id: list[str] | None = Form(
        None, description="Petición que cierra cada archivo (cadena vacía si ninguna)"
    ),
) -> UploadDocumentsResponse:
    """Acepta VARIOS archivos de una vez, cada uno con su tipo y su petición opcional.

    El nombre del campo sigue siendo `file` (y `doc_type`) a propósito: una subida de un
    solo archivo, la que ya usaba la consola, llega exactamente igual y sigue recibiendo el
    expediente completo. Lo nuevo es que la respuesta trae además el desenlace de CADA
    archivo frente al cruce, que es lo que la pantalla de peticiones necesita para decir
    "este empareja y abre una discrepancia" en vez de "listo".

    Las listas van en paralelo y se exige que midan lo mismo: un `doc_type` de más o de
    menos sería un archivo clasificado con el tipo de otro, y de ahí sale un certificado
    leído con el lector equivocado.
    """
    if len(doc_type) != len(file):
        raise ValidationError(
            "Cada archivo tiene que venir con su tipo de documento.",
            archivos=len(file),
            tipos=len(doc_type),
        )
    if peticion_id is not None and len(peticion_id) != len(file):
        raise ValidationError(
            "Si se manda la petición de un archivo, hay que mandarla para todos.",
            archivos=len(file),
            peticiones=len(peticion_id),
        )

    subidos: list[Subido] = []
    detail = None
    for indice, subido in enumerate(file):
        nombre = subido.filename or f"documento-{indice + 1}"
        content = await subido.read()
        pedida = peticion_id[indice].strip() if peticion_id is not None else ""
        # El SHA se calcula acá, sobre los bytes que llegaron, y viaja POR ÍNDICE: es la
        # misma llave con que el cruce registra las versiones de un documento, y es lo que
        # distingue dos archivos que llegan con el mismo nombre en una sola request.
        sha = hashlib.sha256(content).hexdigest()[:12]
        try:
            detail = await container.case_service.add_client_upload(
                case_id=case_id, doc_type=doc_type[indice], content=content, filename=nombre
            )
        except DeclarasError:
            # Las fallas previstas del expediente (declaración que no existe, identidad que
            # no cuadra) valen para toda la subida, no para un archivo: se dejan subir.
            raise
        except Exception:
            # Un archivo que revienta por algo no previsto NO puede abortar la request: los
            # anteriores ya quedaron guardados, el cruce no correría, y el cliente
            # reintentaría duplicando todo (F7). Se reporta ese archivo como no recibido —
            # con su traza en el log— y los demás siguen su camino.
            log.exception("case.upload_failed", case_id=str(case_id), archivo=nombre)
            subidos.append(
                Subido(
                    archivo=nombre,
                    doc_type=doc_type[indice],
                    peticion_id=pedida or None,
                    sha=None,
                    motivo=(
                        "No se pudo recibir este archivo. Los demás sí entraron; hay que "
                        "volver a mandar solo este."
                    ),
                )
            )
            continue
        subidos.append(
            Subido(
                archivo=nombre,
                doc_type=doc_type[indice],
                peticion_id=pedida or None,
                sha=sha,
            )
        )

    if detail is None:
        # Todos los archivos fallaron: el expediente se devuelve igual, con el desenlace
        # de cada uno, en vez de un 500 que no dice cuál entró y cuál no.
        detail = await container.case_service.get_detail(case_id)

    # El cruce se rehace UNA vez con todos los archivos ya dentro, no uno por archivo:
    # incorporar de a uno y recalcular en medio dejaría estados intermedios persistidos y
    # una versión de la liquidación por archivo.
    try:
        resultados = await container.conciliacion_service.incorporar_documentos(case_id, subidos)
    except ConflictoDeConcurrenciaError:
        # Los archivos YA quedaron guardados; lo que no alcanzó a correr es el cruce. Un 409
        # acá diría "vuelve a cargarla y repite" y el cliente reenviaría los archivos,
        # duplicándolos. Se responde 200 con la verdad por archivo: entró, no se cruzó, y la
        # acción es conciliar. (Que los renglones queden viejos ya no es peligroso: la huella
        # del expediente hace que borrador, memoria, cerrar y la vigencia se nieguen hasta
        # que alguien concilie.)
        log.info("case.upload_sin_cruce", case_id=str(case_id), archivos=len(subidos))
        resultados = [
            ArchivoIncorporado(
                archivo=s.archivo,
                doc_type=s.doc_type,
                estado=ESTADO_NO_RECIBIDO if s.sha is None else ESTADO_A_BANDEJA,
                peticion_cerrada=None if s.peticion_id is None else False,
                motivo=s.motivo or _MOTIVO_CRUCE_NO_CORRIO,
            )
            for s in subidos
        ]
    return UploadDocumentsResponse(
        **CaseDetailResponse.from_domain(
            detail, download_url_builder=_download_url
        ).model_dump(),
        resultados=[ArchivoIncorporadoResponse.from_resultado(r) for r in resultados],
    )


@router.post(
    "/cases/{case_id}/flags/{flag_id}/resolve",
    response_model=CaseFlagResponse,
    summary="Marca un flag como resuelto",
)
async def resolve_flag(
    case_id: UUID,
    flag_id: UUID,
    payload: ResolveFlagRequest,
    container: ContainerDep,
    _auth: ApiKeyDep,
) -> CaseFlagResponse:
    flag = await container.case_service.resolve_flag(
        case_id=case_id, flag_id=flag_id, note=payload.note
    )
    return CaseFlagResponse.from_domain(flag)


@router.get(
    "/clients",
    response_model=list[ClientResponse],
    summary="Lista los clientes (para la consola del contador)",
)
async def list_clients(
    container: ContainerDep, _auth: ApiKeyDep, limit: int = 50, offset: int = 0
) -> list[ClientResponse]:
    clients = await container.clients.list_all(limit=limit, offset=offset)
    return [ClientResponse.from_domain(c) for c in clients]


@router.get(
    "/clients/{client_id}/cases",
    response_model=list[CaseSummaryResponse],
    summary="Los expedientes de un cliente, por todos los anios",
)
async def list_client_cases(
    client_id: UUID, container: ContainerDep, _auth: ApiKeyDep
) -> list[CaseSummaryResponse]:
    cases = await container.cases.list_for_client(client_id)
    return [
        CaseSummaryResponse(
            id=c.id,
            client_id=c.client_id,
            tax_year=c.tax_year,
            status=c.status,
            updated_at=c.updated_at,
        )
        for c in cases
    ]
