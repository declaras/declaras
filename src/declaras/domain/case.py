"""El expediente: el agregado que amarra todo lo que Clara sabe de un caso.

Cliente -> Expediente (uno por cliente y anio gravable) -> Documentos (bajados del portal
o subidos por el cliente) -> lecturas estructuradas de cada documento -> flags que un
contador debe revisar -> bitacora de todo lo que paso.

Es el corazon del sistema: el conector DIAN y el servicio de lectura de documentos
producen datos sueltos (un job de extraccion, una lectura de un archivo); el expediente es
lo que los organiza por cliente y los deja consultables para el motor tributario y para
la consola del contador.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from declaras.documents.models import DocumentReading
from declaras.domain.models import IdDocumentKind


class Client(BaseModel):
    """El contribuyente. Persiste entre anios: sus expedientes son los que son anuales."""

    id: UUID = Field(default_factory=uuid4)
    id_kind: IdDocumentKind = IdDocumentKind.CC
    id_number: str
    full_name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def subject_key(self) -> str:
        return f"{self.id_kind.value}-{self.id_number}"


class CaseStatus(StrEnum):
    """El recorrido del expediente. No repite el estado del job de extraccion (ese vive
    en el conector); esto es el estado del CASO como unidad de negocio."""

    OPEN = "OPEN"
    EXTRACTING = "EXTRACTING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    DRAFT_READY = "DRAFT_READY"
    SUBMITTED = "SUBMITTED"
    CLOSED = "CLOSED"

    @property
    def is_terminal(self) -> bool:
        return self in {CaseStatus.SUBMITTED, CaseStatus.CLOSED}


class CaseDocumentSource(StrEnum):
    """De donde vino el documento: cambia como se audita y como se puede volver a pedir."""

    DIAN_PORTAL = "DIAN_PORTAL"
    CLIENT_UPLOAD = "CLIENT_UPLOAD"


class CaseDocument(BaseModel):
    """Un documento dentro de un expediente, con su lectura si ya se proceso.

    `doc_type` es texto libre y no el enum `DocumentType` del conector a proposito: los
    documentos del portal (RUT, EXOGENA...) son un catalogo fijo y pequeno, pero los que
    sube el cliente (certificado de intereses, registro civil, PILA...) son un catalogo
    de producto en evolucion. Forzarlos al mismo enum obligaria a tocar el dominio del
    conector cada vez que el producto agregue un tipo de certificado nuevo.
    """

    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    doc_type: str
    source: CaseDocumentSource
    storage_uri: str
    filename: str
    content_sha256: str
    added_at: datetime
    reading: DocumentReading | None = None
    # Una consulta mas reciente trajo este mismo documento y lo reemplazo. Se conserva
    # para la auditoria, pero no es el vigente.
    superseded_at: datetime | None = None
    # Referencia al job de extraccion DIAN, cuando el origen es el portal: permite volver
    # a consultar el detalle completo de esa extraccion (incluidas sus fallas parciales).
    extraction_job_id: UUID | None = None


class FlagSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class CaseFlag(BaseModel):
    """Algo que un contador debe mirar antes de dar el expediente por bueno."""

    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    code: str
    message: str
    severity: FlagSeverity = FlagSeverity.WARNING
    source_document_id: UUID | None = None
    raised_at: datetime
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None


class CaseEvent(BaseModel):
    """Bitacora de auditoria: registro append-only de todo lo que le paso al expediente.

    Nunca se edita ni se borra un evento: es la memoria de por que el expediente quedo
    como quedo, y respalda la garantia del producto ante un requerimiento futuro.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    kind: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class CaseDetail(BaseModel):
    """El expediente completo, listo para la consola del contador: caso, cliente,
    documentos con su lectura, flags y bitacora, todo en una sola consulta.

    `documents` trae solo los vigentes. Los reemplazados por una consulta posterior siguen
    en la base y en la bitacora, y se consultan por `superseded_documents`.
    """

    case: Case
    client: Client
    documents: list[CaseDocument] = Field(default_factory=list)
    superseded_documents: list[CaseDocument] = Field(default_factory=list)
    flags: list[CaseFlag] = Field(default_factory=list)
    events: list[CaseEvent] = Field(default_factory=list)

    @property
    def open_flags(self) -> list[CaseFlag]:
        return [f for f in self.flags if not f.is_resolved]


class Case(BaseModel):
    """Un expediente: el trabajo de un cliente para un anio gravable."""

    id: UUID = Field(default_factory=uuid4)
    client_id: UUID
    tax_year: int
    status: CaseStatus = CaseStatus.OPEN
    created_at: datetime
    updated_at: datetime
