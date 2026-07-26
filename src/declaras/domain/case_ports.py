"""Puertos del expediente. Los adaptadores de persistencia los implementan."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from declaras.documents.models import DocumentReading
from declaras.domain.case import (
    Case,
    CaseDetail,
    CaseDocument,
    CaseDocumentSource,
    CaseEvent,
    CaseFlag,
    CaseStatus,
    Client,
    FlagSeverity,
)
from declaras.domain.models import IdDocumentKind


@runtime_checkable
class ClientRepository(Protocol):
    async def get_or_create(
        self,
        *,
        id_kind: IdDocumentKind,
        id_number: str,
        full_name: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Client:
        """Devuelve el cliente existente por su documento, o lo crea si es la primera vez."""
        ...

    async def get(self, client_id: UUID) -> Client | None: ...

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[Client]: ...


@runtime_checkable
class CaseRepository(Protocol):
    async def create(self, *, client_id: UUID, tax_year: int) -> Case: ...

    async def get(self, case_id: UUID) -> Case | None: ...

    async def get_detail(self, case_id: UUID) -> CaseDetail | None:
        """El expediente completo: cliente, documentos, flags y bitacora en una consulta."""
        ...

    async def list_for_client(self, client_id: UUID) -> list[Case]: ...

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[Case]: ...

    async def transition(self, case_id: UUID, *, status: CaseStatus) -> Case: ...

    async def add_document(
        self,
        *,
        case_id: UUID,
        doc_type: str,
        source: CaseDocumentSource,
        storage_uri: str,
        filename: str,
        content_sha256: str,
        extraction_job_id: UUID | None = None,
    ) -> CaseDocument: ...

    async def attach_reading(self, document_id: UUID, reading: DocumentReading) -> CaseDocument: ...

    async def add_flag(
        self,
        *,
        case_id: UUID,
        code: str,
        message: str,
        severity: FlagSeverity = FlagSeverity.WARNING,
        source_document_id: UUID | None = None,
    ) -> CaseFlag: ...

    async def resolve_flag(self, flag_id: UUID, *, note: str | None = None) -> CaseFlag: ...

    async def add_event(
        self, *, case_id: UUID, kind: str, message: str, payload: dict[str, Any] | None = None
    ) -> CaseEvent: ...
