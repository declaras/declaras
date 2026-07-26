"""Entidades y objetos de valor del dominio. Sin I/O, sin dependencias de framework."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class IdDocumentKind(StrEnum):
    CC = "CC"
    CE = "CE"
    NIT = "NIT"
    PASSPORT = "PA"


class DocumentType(StrEnum):
    """Tipos de documento que el conector extrae del portal.

    `CLIENT_DOCUMENT` es distinto: es el cubo generico de almacenamiento para lo que el
    cliente sube por chat (certificados, registro civil...), cuyo catalogo real y con
    significado de producto vive en `CaseDocument.doc_type` (texto libre). Este marcador
    solo existe para que la convencion de rutas de almacenamiento, pensada para el
    conector, tambien sirva para esos archivos sin forzar un catalogo cerrado aqui.
    """

    RUT = "RUT"
    EXOGENA = "EXOGENA"
    PRIOR_RETURN = "PRIOR_RETURN"
    SUGGESTED_RETURN = "SUGGESTED_RETURN"
    EINVOICE_SUMMARY = "EINVOICE_SUMMARY"
    EVIDENCE = "EVIDENCE"
    CLIENT_DOCUMENT = "CLIENT_DOCUMENT"


# Nombre legible de cada tipo de documento, para los mensajes que ve una persona.
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    DocumentType.RUT: "el RUT",
    DocumentType.EXOGENA: "la información exógena",
    DocumentType.PRIOR_RETURN: "la declaración del año anterior",
    DocumentType.SUGGESTED_RETURN: "el borrador sugerido por la DIAN",
    DocumentType.EINVOICE_SUMMARY: "el resumen de facturas electrónicas",
    DocumentType.EVIDENCE: "la evidencia de auditoría",
    DocumentType.CLIENT_DOCUMENT: "un documento del cliente",
}


def document_label(doc_type: str) -> str:
    """Nombre legible de un tipo de documento, o el propio codigo si no se conoce."""
    return DOCUMENT_TYPE_LABELS.get(doc_type, doc_type.replace("_", " ").lower())


class JobKind(StrEnum):
    DIAN_EXTRACTION = "DIAN_EXTRACTION"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_CHALLENGE = "AWAITING_CHALLENGE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class ChallengeKind(StrEnum):
    """Lo que el portal puede pedir y solo el contribuyente puede responder."""

    EMAIL_CODE = "EMAIL_CODE"
    SECURITY_QUESTIONS = "SECURITY_QUESTIONS"


class TaxpayerRef(BaseModel):
    """Identificacion del contribuyente y periodo a consultar."""

    model_config = ConfigDict(frozen=True)

    id_kind: IdDocumentKind = IdDocumentKind.CC
    id_number: str = Field(min_length=5, max_length=15)
    tax_year: int = Field(ge=2015, le=2100)

    @field_validator("id_number")
    @classmethod
    def _only_digits_for_local_ids(cls, value: str, info: Any) -> str:
        kind = (info.data or {}).get("id_kind")
        if kind in {IdDocumentKind.CC, IdDocumentKind.NIT} and not value.isdigit():
            raise ValueError("el numero de documento debe ser numerico")
        return value.strip()

    @property
    def subject_key(self) -> str:
        """Llave estable del sujeto, usada para rutas de almacenamiento y guardas."""
        return f"{self.id_kind.value}-{self.id_number}"


class DianCredentials(BaseModel):
    """Credenciales del portal. Nunca se persisten ni se serializan en claro."""

    model_config = ConfigDict(frozen=True)

    id_kind: IdDocumentKind = IdDocumentKind.CC
    id_number: str = Field(min_length=5, max_length=15)
    password: SecretStr
    # El portal distingue entre entrar "a nombre propio" o representando a un NIT.
    on_behalf_of_nit: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - proteccion de logs
        return f"DianCredentials(id_number='***{self.id_number[-3:]}', password='***')"

    __str__ = __repr__


class IdentityChallenge(BaseModel):
    """Reto del portal que exige input del contribuyente (patron relevo)."""

    kind: ChallengeKind
    prompt: str
    options: list[str] = Field(default_factory=list)
    issued_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class ChallengeAnswer(BaseModel):
    answers: list[str] = Field(min_length=1)


class RawDocument(BaseModel):
    """Documento tal como sale del portal, antes de almacenarse."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    doc_type: DocumentType
    filename: str
    content: bytes
    content_type: str = "application/pdf"
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoredDocument(BaseModel):
    """Documento ya persistido en el almacenamiento."""

    id: UUID = Field(default_factory=uuid4)
    doc_type: DocumentType
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_uri: str
    captured_at: datetime
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionRequest(BaseModel):
    """Peticion de extraccion. Las credenciales viajan aparte y no se persisten."""

    taxpayer: TaxpayerRef
    doc_types: list[DocumentType] = Field(
        default_factory=lambda: [
            DocumentType.RUT,
            DocumentType.EXOGENA,
            DocumentType.PRIOR_RETURN,
            DocumentType.SUGGESTED_RETURN,
            DocumentType.EINVOICE_SUMMARY,
        ]
    )
    callback_url: str | None = None

    @field_validator("doc_types")
    @classmethod
    def _reject_non_requestable(cls, value: list[DocumentType]) -> list[DocumentType]:
        non_requestable = {DocumentType.EVIDENCE, DocumentType.CLIENT_DOCUMENT}
        found = non_requestable & set(value)
        if found:
            names = ", ".join(sorted(d.value for d in found))
            raise ValueError(f"{names} no son solicitables al conector DIAN")
        return list(dict.fromkeys(value))


class DocumentFailure(BaseModel):
    """Un documento que no se pudo traer, sin tumbar la extraccion completa."""

    doc_type: DocumentType
    code: str
    message: str
    retryable: bool


class ExtractionResult(BaseModel):
    """Resultado de la extraccion: lo que se logro y lo que no."""

    taxpayer: TaxpayerRef
    documents: list[StoredDocument] = Field(default_factory=list)
    failures: list[DocumentFailure] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime

    @property
    def is_partial(self) -> bool:
        return bool(self.failures) and bool(self.documents)


class Job(BaseModel):
    """Unidad de trabajo asincrona. Nunca contiene credenciales."""

    id: UUID = Field(default_factory=uuid4)
    kind: JobKind
    status: JobStatus = JobStatus.QUEUED
    request: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    challenge: IdentityChallenge | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    leased_until: datetime | None = None
    worker_id: str | None = None

    def with_status(self, status: JobStatus, now: datetime) -> Self:
        return self.model_copy(update={"status": status, "updated_at": now})
