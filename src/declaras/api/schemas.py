"""Contratos HTTP. Separados del dominio a proposito: la API puede evolucionar sin
arrastrar el modelo interno, y el agente que nos consume depende solo de esto.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from declaras.documents.models import DocumentReading
from declaras.domain.models import (
    DOCUMENTOS_QUE_SE_PIDEN,
    DianCredentials,
    DocumentType,
    ExtractionRequest,
    IdDocumentKind,
    Job,
    JobStatus,
    StepState,
    TaxpayerRef,
)
from declaras.domain.tax_calendar import default_tax_year


# QUE DOCUMENTOS SE PIDEN LO DECIDE EL DOMINIO, NO ESTA CAPA: `DOCUMENTOS_QUE_SE_PIDEN`.
def _doc_types_por_defecto() -> list[DocumentType]:
    return list(DOCUMENTOS_QUE_SE_PIDEN)


class CreateExtractionRequest(BaseModel):
    """Peticion de extraccion.

    La clave de la DIAN se recibe aca, se usa en memoria y nunca se persiste ni se
    escribe en logs.
    """

    id_kind: IdDocumentKind = Field(default=IdDocumentKind.CC, examples=["CC"])
    id_number: str = Field(min_length=5, max_length=15, examples=["1020304050"])
    dian_password: SecretStr = Field(examples=["clave-del-portal"])
    tax_year: int | None = Field(
        default=None,
        ge=2015,
        le=2100,
        examples=[None],
        description=(
            "Opcional. Si no se envia, se usa el anio gravable que corresponde declarar "
            "hoy (el anio anterior al actual). Se envia explicito solo para poner al dia "
            "declaraciones de anios pasados."
        ),
    )
    on_behalf_of_nit: str | None = Field(default=None, examples=[None])
    doc_types: list[DocumentType] = Field(default_factory=_doc_types_por_defecto)
    callback_url: HttpUrl | None = Field(
        default=None,
        description="Si se envia, avisamos aqui cuando el job termine.",
    )

    def to_domain(self) -> tuple[ExtractionRequest, DianCredentials]:
        taxpayer = TaxpayerRef(
            id_kind=self.id_kind,
            id_number=self.id_number,
            tax_year=self.tax_year or default_tax_year(),
        )
        request = ExtractionRequest(
            taxpayer=taxpayer,
            doc_types=self.doc_types,
            callback_url=str(self.callback_url) if self.callback_url else None,
        )
        credentials = DianCredentials(
            id_kind=self.id_kind,
            id_number=self.id_number,
            password=self.dian_password,
            on_behalf_of_nit=self.on_behalf_of_nit,
        )
        return request, credentials


class ChallengeAnswerRequest(BaseModel):
    """Respuesta del contribuyente al reto de identidad (patron relevo)."""

    answers: list[str] = Field(min_length=1, examples=[["1234"]])


class DocumentResponse(BaseModel):
    doc_type: DocumentType
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_uri: str
    captured_at: datetime
    download_url: str


class FailureResponse(BaseModel):
    doc_type: DocumentType
    code: str
    message: str
    retryable: bool


class ChallengeResponse(BaseModel):
    kind: str
    prompt: str
    options: list[str]
    expires_at: datetime


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class StepResponse(BaseModel):
    """Un paso del trabajo, para poder mostrar en que va mientras corre."""

    key: str
    label: str
    state: StepState
    detail: str | None = None


class ExtractionResponse(BaseModel):
    """Estado completo de una extraccion. Es la respuesta que el agente consulta."""

    job_id: UUID
    status: JobStatus
    # En que va, paso a paso. Se publica mientras el trabajo corre, no solo al final: contra el
    # portal real tarda cerca de medio minuto, y una pantalla quieta no dice si esta vivo.
    progress: list[StepResponse] = Field(default_factory=list)
    attempts: int
    created_at: datetime
    updated_at: datetime
    taxpayer: dict[str, Any]
    documents: list[DocumentResponse] = Field(default_factory=list)
    failures: list[FailureResponse] = Field(default_factory=list)
    challenge: ChallengeResponse | None = None
    error: ErrorResponse | None = None

    @classmethod
    def from_job(cls, job: Job, *, download_url_builder: Any) -> ExtractionResponse:
        result = job.result or {}
        documents = [
            DocumentResponse(**doc, download_url=download_url_builder(doc["storage_uri"]))
            for doc in result.get("documents", [])
        ]
        return cls(
            job_id=job.id,
            status=job.status,
            progress=[StepResponse(**paso.model_dump()) for paso in job.progress],
            attempts=job.attempts,
            created_at=job.created_at,
            updated_at=job.updated_at,
            taxpayer=job.request.get("taxpayer", {}),
            documents=documents,
            failures=[FailureResponse(**f) for f in result.get("failures", [])],
            challenge=(
                ChallengeResponse(
                    kind=job.challenge.kind.value,
                    prompt=job.challenge.prompt,
                    options=job.challenge.options,
                    expires_at=job.challenge.expires_at,
                )
                if job.challenge
                else None
            ),
            error=ErrorResponse(**job.error) if job.error else None,
        )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    dian_adapter: str
    worker_enabled: bool


class ReadStoredDocumentRequest(BaseModel):
    """Referencia de un documento que el conector DIAN ya descargo."""

    storage_uri: str = Field(examples=["file://cc-abc123/2025/exogena/f64a0655.xlsx"])
    doc_type: str = Field(examples=["EXOGENA"])


class ExtractedFieldResponse(BaseModel):
    name: str
    value: Any
    confidence: float
    source: str | None = None
    unit: str | None = None


class ExtractedRowResponse(BaseModel):
    values: dict[str, Any]
    source: str | None = None


class ReadingWarningResponse(BaseModel):
    code: str
    message: str
    source: str | None = None


class DocumentReadingResponse(BaseModel):
    """Resultado de leer un documento: valores con su procedencia y confianza."""

    doc_type: str
    parser: str
    content_sha256: str
    fields: list[ExtractedFieldResponse]
    rows: list[ExtractedRowResponse]
    warnings: list[ReadingWarningResponse]

    @classmethod
    def from_reading(cls, reading: DocumentReading) -> DocumentReadingResponse:
        return cls(
            doc_type=reading.doc_type,
            parser=reading.parser,
            content_sha256=reading.content_sha256,
            fields=[ExtractedFieldResponse(**f.model_dump()) for f in reading.fields],
            rows=[ExtractedRowResponse(**r.model_dump()) for r in reading.rows],
            warnings=[ReadingWarningResponse(**w.model_dump()) for w in reading.warnings],
        )
