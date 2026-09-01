"""Repositorios del expediente sobre SQLAlchemy.

Dos repositorios (clientes y expedientes) porque tienen ciclos de vida distintos: un
cliente persiste entre anios, un expediente es del cliente y de un anio gravable. El
punto delicado es `get_or_create`: dos hilos no deben crear el mismo cliente dos veces, lo
que se resuelve con la restriccion unica de la tabla mas una consulta de respaldo si el
insert choca (el mismo patron defensivo que `claim_next` en el repositorio de jobs).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from declaras.adapters.persistence.tables import (
    CaseDocumentRow,
    CaseEventRow,
    CaseFlagRow,
    CaseRow,
    ClientRow,
)
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
from declaras.domain.errors import CaseAlreadyExistsError, CaseNotFoundError, FlagNotFoundError
from declaras.domain.models import IdDocumentKind


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _client_from_row(row: ClientRow) -> Client:
    return Client(
        id=UUID(row.id),
        id_kind=IdDocumentKind(row.id_kind),
        id_number=row.id_number,
        full_name=row.full_name,
        phone_number=row.phone_number,
        email=row.email,
        created_at=_as_utc(row.created_at) or _utcnow(),
        updated_at=_as_utc(row.updated_at) or _utcnow(),
    )


def _case_from_row(row: CaseRow) -> Case:
    return Case(
        id=UUID(row.id),
        client_id=UUID(row.client_id),
        tax_year=row.tax_year,
        status=CaseStatus(row.status),
        created_at=_as_utc(row.created_at) or _utcnow(),
        updated_at=_as_utc(row.updated_at) or _utcnow(),
    )


def _document_from_row(row: CaseDocumentRow) -> CaseDocument:
    return CaseDocument(
        id=UUID(row.id),
        case_id=UUID(row.case_id),
        doc_type=row.doc_type,
        source=CaseDocumentSource(row.source),
        storage_uri=row.storage_uri,
        filename=row.filename,
        content_sha256=row.content_sha256,
        added_at=_as_utc(row.added_at) or _utcnow(),
        reading=DocumentReading.model_validate(row.reading_json) if row.reading_json else None,
        extraction_job_id=UUID(row.extraction_job_id) if row.extraction_job_id else None,
        superseded_at=_as_utc(row.superseded_at),
    )


def _flag_from_row(row: CaseFlagRow) -> CaseFlag:
    return CaseFlag(
        id=UUID(row.id),
        case_id=UUID(row.case_id),
        code=row.code,
        message=row.message,
        severity=FlagSeverity(row.severity),
        source_document_id=UUID(row.source_document_id) if row.source_document_id else None,
        raised_at=_as_utc(row.raised_at) or _utcnow(),
        resolved_at=_as_utc(row.resolved_at),
        resolution_note=row.resolution_note,
    )


def _event_from_row(row: CaseEventRow) -> CaseEvent:
    return CaseEvent(
        id=UUID(row.id),
        case_id=UUID(row.case_id),
        kind=row.kind,
        message=row.message,
        payload=row.payload or {},
        occurred_at=_as_utc(row.occurred_at) or _utcnow(),
    )


class SqlClientRepository:
    """Implementa ClientRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get_or_create(
        self,
        *,
        id_kind: IdDocumentKind,
        id_number: str,
        full_name: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Client:
        existing = await self._find(id_kind, id_number)
        if existing is not None:
            return existing

        now = _utcnow()
        row = ClientRow(
            id=str(uuid4()),
            id_kind=id_kind.value,
            id_number=id_number,
            full_name=full_name,
            phone_number=phone_number,
            email=email,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
        except IntegrityError:
            # Otro hilo lo creo entre la consulta y el insert: se toma el que ya quedo.
            existing = await self._find(id_kind, id_number)
            if existing is not None:
                return existing
            raise
        return _client_from_row(row)

    async def get(self, client_id: UUID) -> Client | None:
        async with self._sessions() as session:
            row = await session.get(ClientRow, str(client_id))
            return _client_from_row(row) if row else None

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[Client]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(ClientRow)
                    .order_by(ClientRow.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
            return [_client_from_row(r) for r in rows]

    async def guardar_clave(self, client_id: UUID, cifrada: str) -> None:
        """Guarda la clave del portal, YA CIFRADA. Aca nunca se ve en claro."""
        async with self._sessions() as session, session.begin():
            row = await session.get(ClientRow, str(client_id))
            if row is None:
                raise CaseNotFoundError(client_id=str(client_id))
            row.dian_password_cifrada = cifrada
            row.dian_password_guardada_at = _utcnow()

    async def leer_clave(self, client_id: UUID) -> str | None:
        """La clave cifrada, o None si no hay ninguna guardada."""
        async with self._sessions() as session:
            row = await session.get(ClientRow, str(client_id))
            return row.dian_password_cifrada if row else None

    async def borrar_clave(self, client_id: UUID) -> bool:
        """Olvida la clave. `False` si no habia ninguna.

        Una clave guardada sin forma de borrarla no es una funcion, es una trampa: quien la
        confio tiene que poder retirarla, y eso vale tanto para el titular como para el
        despliegue que quiera limpiar.
        """
        async with self._sessions() as session, session.begin():
            row = await session.get(ClientRow, str(client_id))
            if row is None or row.dian_password_cifrada is None:
                return False
            row.dian_password_cifrada = None
            row.dian_password_guardada_at = None
            return True

    async def _find(self, id_kind: IdDocumentKind, id_number: str) -> Client | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(ClientRow).where(
                        ClientRow.id_kind == id_kind.value, ClientRow.id_number == id_number
                    )
                )
            ).scalar_one_or_none()
            return _client_from_row(row) if row else None


class SqlCaseRepository:
    """Implementa CaseRepository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, *, client_id: UUID, tax_year: int) -> Case:
        now = _utcnow()
        row = CaseRow(
            id=str(uuid4()),
            client_id=str(client_id),
            tax_year=tax_year,
            status=CaseStatus.OPEN.value,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
        except IntegrityError as exc:
            raise CaseAlreadyExistsError(client_id=str(client_id), tax_year=tax_year) from exc
        return _case_from_row(row)

    async def get(self, case_id: UUID) -> Case | None:
        async with self._sessions() as session:
            row = await session.get(CaseRow, str(case_id))
            return _case_from_row(row) if row else None

    async def get_detail(self, case_id: UUID) -> CaseDetail | None:
        async with self._sessions() as session:
            case_row = await session.get(CaseRow, str(case_id))
            if case_row is None:
                return None
            client_row = await session.get(ClientRow, case_row.client_id)
            if client_row is None:  # pragma: no cover - integridad referencial
                return None

            documents = (
                await session.execute(
                    select(CaseDocumentRow)
                    .where(CaseDocumentRow.case_id == str(case_id))
                    .order_by(CaseDocumentRow.added_at)
                )
            ).scalars()
            flags = (
                await session.execute(
                    select(CaseFlagRow)
                    .where(CaseFlagRow.case_id == str(case_id))
                    .order_by(CaseFlagRow.raised_at)
                )
            ).scalars()
            events = (
                await session.execute(
                    select(CaseEventRow)
                    .where(CaseEventRow.case_id == str(case_id))
                    .order_by(CaseEventRow.occurred_at)
                )
            ).scalars()

            todos = [_document_from_row(d) for d in documents]
            return CaseDetail(
                case=_case_from_row(case_row),
                client=_client_from_row(client_row),
                documents=[d for d in todos if d.superseded_at is None],
                superseded_documents=[d for d in todos if d.superseded_at is not None],
                flags=[_flag_from_row(f) for f in flags],
                events=[_event_from_row(e) for e in events],
            )

    async def list_for_client(self, client_id: UUID) -> list[Case]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(CaseRow)
                    .where(CaseRow.client_id == str(client_id))
                    .order_by(CaseRow.tax_year.desc())
                )
            ).scalars()
            return [_case_from_row(r) for r in rows]

    async def list_all(self, *, limit: int = 50, offset: int = 0) -> list[Case]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(CaseRow).order_by(CaseRow.updated_at.desc()).limit(limit).offset(offset)
                )
            ).scalars()
            return [_case_from_row(r) for r in rows]

    async def transition(self, case_id: UUID, *, status: CaseStatus) -> Case:
        return await self._update_case(case_id, status=status.value, updated_at=_utcnow())

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
    ) -> CaseDocument:
        row = CaseDocumentRow(
            id=str(uuid4()),
            case_id=str(case_id),
            doc_type=doc_type,
            source=source.value,
            storage_uri=storage_uri,
            filename=filename,
            content_sha256=content_sha256,
            added_at=_utcnow(),
            extraction_job_id=str(extraction_job_id) if extraction_job_id else None,
        )
        async with self._sessions() as session, session.begin():
            if await session.get(CaseRow, str(case_id)) is None:
                raise CaseNotFoundError(case_id=str(case_id))
            session.add(row)
        return _document_from_row(row)

    async def attach_reading(self, document_id: UUID, reading: DocumentReading) -> CaseDocument:
        async with self._sessions() as session, session.begin():
            row = await session.get(CaseDocumentRow, str(document_id))
            if row is None:
                raise CaseNotFoundError(document_id=str(document_id))
            row.reading_json = reading.model_dump(mode="json")
            await session.flush()
            return _document_from_row(row)

    async def supersede_documents(
        self, *, case_id: UUID, doc_type: str, source: CaseDocumentSource
    ) -> list[CaseDocument]:
        now = _utcnow()
        async with self._sessions() as session, session.begin():
            rows = (
                await session.execute(
                    select(CaseDocumentRow).where(
                        CaseDocumentRow.case_id == str(case_id),
                        CaseDocumentRow.doc_type == doc_type,
                        CaseDocumentRow.source == source.value,
                        CaseDocumentRow.superseded_at.is_(None),
                    )
                )
            ).scalars()
            reemplazados = list(rows)
            for row in reemplazados:
                row.superseded_at = now
            await session.flush()
            return [_document_from_row(r) for r in reemplazados]

    async def add_flag(
        self,
        *,
        case_id: UUID,
        code: str,
        message: str,
        severity: FlagSeverity = FlagSeverity.WARNING,
        source_document_id: UUID | None = None,
    ) -> CaseFlag:
        row = CaseFlagRow(
            id=str(uuid4()),
            case_id=str(case_id),
            code=code,
            message=message,
            severity=severity.value,
            source_document_id=str(source_document_id) if source_document_id else None,
            raised_at=_utcnow(),
        )
        async with self._sessions() as session, session.begin():
            if await session.get(CaseRow, str(case_id)) is None:
                raise CaseNotFoundError(case_id=str(case_id))
            session.add(row)
        return _flag_from_row(row)

    async def resolve_flag(self, flag_id: UUID, *, note: str | None = None) -> CaseFlag:
        async with self._sessions() as session, session.begin():
            row = await session.get(CaseFlagRow, str(flag_id))
            if row is None:
                raise FlagNotFoundError(flag_id=str(flag_id))
            row.resolved_at = _utcnow()
            row.resolution_note = note
            await session.flush()
            return _flag_from_row(row)

    async def add_event(
        self, *, case_id: UUID, kind: str, message: str, payload: dict[str, Any] | None = None
    ) -> CaseEvent:
        row = CaseEventRow(
            id=str(uuid4()),
            case_id=str(case_id),
            kind=kind,
            message=message,
            payload=payload or {},
            occurred_at=_utcnow(),
        )
        async with self._sessions() as session, session.begin():
            if await session.get(CaseRow, str(case_id)) is None:
                raise CaseNotFoundError(case_id=str(case_id))
            session.add(row)
        return _event_from_row(row)

    async def _update_case(self, case_id: UUID, **values: Any) -> Case:
        async with self._sessions() as session, session.begin():
            row = await session.get(CaseRow, str(case_id))
            if row is None:
                raise CaseNotFoundError(case_id=str(case_id))
            for key, value in values.items():
                setattr(row, key, value)
            await session.flush()
            return _case_from_row(row)
