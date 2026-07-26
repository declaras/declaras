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
from declaras.api.deps import ApiKeyDep, ContainerDep
from declaras.domain.errors import JobNotFoundError

router = APIRouter(prefix="/v1", tags=["cases"])


def _download_url(storage_uri: str) -> str:
    return f"/v1/documents/content?uri={quote(storage_uri, safe='')}"


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
    response_model=CaseDetailResponse,
    summary="El cliente sube un documento por chat (foto o PDF)",
)
async def upload_client_document(
    case_id: UUID,
    container: ContainerDep,
    _auth: ApiKeyDep,
    doc_type: str = Form(..., description="Tipo de documento, ej. certificado_intereses_vivienda"),
    file: UploadFile = File(...),
) -> CaseDetailResponse:
    content = await file.read()
    detail = await container.case_service.add_client_upload(
        case_id=case_id, doc_type=doc_type, content=content, filename=file.filename or "documento"
    )
    return CaseDetailResponse.from_domain(detail, download_url_builder=_download_url)


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
    del case_id  # el flag ya trae su propio id; el case_id en la ruta es por legibilidad REST
    flag = await container.case_service.resolve_flag(flag_id, note=payload.note)
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
