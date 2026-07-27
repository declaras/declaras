"""Endpoints del expediente: cliente, caso, documentos, flags y bitacora.

Es la API que consume la consola del contador y el agente conversacional para vincular
lo que el conector DIAN descarga o lo que el cliente sube por chat a un expediente.
"""

from __future__ import annotations

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
from declaras.domain.errors import JobNotFoundError, ValidationError
from declaras.services.case_summary import CaseSummary, build_summary

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

    subidos: list[tuple[str, str, str | None]] = []
    detail = None
    for indice, subido in enumerate(file):
        nombre = subido.filename or f"documento-{indice + 1}"
        content = await subido.read()
        detail = await container.case_service.add_client_upload(
            case_id=case_id, doc_type=doc_type[indice], content=content, filename=nombre
        )
        pedida = peticion_id[indice].strip() if peticion_id is not None else ""
        subidos.append((nombre, doc_type[indice], pedida or None))

    if detail is None:  # pragma: no cover - FastAPI exige al menos un archivo
        detail = await container.case_service.get_detail(case_id)

    # El cruce se rehace UNA vez con todos los archivos ya dentro, no uno por archivo:
    # incorporar de a uno y recalcular en medio dejaría estados intermedios persistidos y
    # una versión de la liquidación por archivo.
    resultados = await container.conciliacion_service.incorporar_documentos(case_id, subidos)
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
