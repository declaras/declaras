"""Endpoints de extraccion DIAN.

El contrato es asincrono a proposito: una extraccion tarda minutos y el portal se cae,
asi que POST encola y devuelve 202, y el estado se consulta con GET (o llega por
callback). Nunca se deja al cliente esperando en una conexion abierta.
"""

from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Response, status

from declaras.api.deps import AutenticadoDep, ContainerDep, ExtractionDep
from declaras.api.schemas import (
    ChallengeAnswerRequest,
    CreateExtractionRequest,
    ExtractionResponse,
)
from declaras.domain.errors import JobNotFoundError, JobStateConflictError
from declaras.domain.models import ChallengeAnswer, JobStatus

router = APIRouter(prefix="/v1/extractions", tags=["extractions"])


def _download_url(storage_uri: str) -> str:
    return f"/v1/documents/content?uri={quote(storage_uri, safe='')}"


def _render(job: object) -> ExtractionResponse:
    return ExtractionResponse.from_job(job, download_url_builder=_download_url)  # type: ignore[arg-type]


@router.post(
    "",
    response_model=ExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encola una extraccion de informacion en el portal de la DIAN",
)
async def create_extraction(
    payload: CreateExtractionRequest,
    extraction: ExtractionDep,
    _auth: AutenticadoDep,
    response: Response,
) -> ExtractionResponse:
    request, credentials = payload.to_domain()
    job = await extraction.enqueue(request, credentials)
    response.headers["Location"] = f"/v1/extractions/{job.id}"
    return _render(job)


@router.get(
    "/{job_id}",
    response_model=ExtractionResponse,
    summary="Consulta el estado y el resultado de una extraccion",
)
async def get_extraction(
    job_id: UUID,
    container: ContainerDep,
    _auth: AutenticadoDep,
) -> ExtractionResponse:
    job = await container.jobs.get(job_id)
    if job is None:
        raise JobNotFoundError(job_id=str(job_id))
    return _render(job)


@router.post(
    "/{job_id}/challenge",
    response_model=ExtractionResponse,
    summary="Responde la verificacion de identidad que pidio el portal",
)
async def answer_challenge(
    job_id: UUID,
    payload: ChallengeAnswerRequest,
    extraction: ExtractionDep,
    _auth: AutenticadoDep,
) -> ExtractionResponse:
    job = await extraction.submit_challenge_answer(job_id, ChallengeAnswer(answers=payload.answers))
    return _render(job)


@router.post(
    "/{job_id}/cancel",
    response_model=ExtractionResponse,
    summary="Cancela una extraccion que todavia no termina",
)
async def cancel_extraction(
    job_id: UUID,
    container: ContainerDep,
    _auth: AutenticadoDep,
) -> ExtractionResponse:
    job = await container.jobs.get(job_id)
    if job is None:
        raise JobNotFoundError(job_id=str(job_id))
    if job.status.is_terminal:
        raise JobStateConflictError("El trabajo ya terminó.", status=job.status.value)

    await container.vault.discard(job_id)
    await container.registry.discard(job_id)
    cancelled = await container.jobs.transition(job_id, status=JobStatus.CANCELLED)
    return _render(cancelled)
