"""Contratos HTTP del expediente. Separados de schemas.py porque son un dominio propio
(cliente + expediente + documentos + flags + bitacora), no la extraccion DIAN."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from declaras.api.schemas import DocumentReadingResponse
from declaras.domain.case import (
    CaseDetail,
    CaseDocument,
    CaseDocumentSource,
    CaseEvent,
    CaseFlag,
    CaseStatus,
    Client,
    FlagSeverity,
)
from declaras.domain.errors import ValidationError
from declaras.domain.models import IdDocumentKind, TaxpayerRef
from declaras.tax.vencimientos import dos_ultimos_digitos, vencimiento_de


class OpenCaseRequest(BaseModel):
    """Abre una declaracion. Crea el cliente si es la primera vez que se ve su documento."""

    id_kind: IdDocumentKind = IdDocumentKind.CC
    id_number: str = Field(min_length=5, max_length=15, examples=["1020304050"])
    tax_year: int = Field(ge=2015, le=2100, examples=[2025])
    full_name: str | None = None
    phone_number: str | None = Field(default=None, description="Numero de WhatsApp")
    email: str | None = None

    @model_validator(mode="after")
    def _cumple_las_reglas_del_dominio(self) -> OpenCaseRequest:
        """Valida la identidad construyendo el objeto del dominio, en vez de repetir sus reglas.

        Antes este esquema repetia los limites de cada campo pero no la regla de que una cedula
        es solo digitos, que vive en `TaxpayerRef`. Las dos definiciones se desincronizaron y la
        API aceptaba abrir una declaracion a nombre de "abc123": entraba basura al sistema y
        reventaba mas adelante, al consultar la DIAN, de una forma que no apuntaba al origen.

        Construir el objeto del dominio hace imposible que vuelvan a separarse.
        """
        TaxpayerRef(id_kind=self.id_kind, id_number=self.id_number, tax_year=self.tax_year)
        return self


class LinkExtractionRequest(BaseModel):
    """Vincula al expediente el resultado de un job de extraccion DIAN ya terminado."""

    job_id: UUID


class UploadClientDocumentRequest(BaseModel):
    doc_type: str = Field(examples=["certificado_intereses_vivienda"])


class ResolveFlagRequest(BaseModel):
    note: str | None = None


class ClientResponse(BaseModel):
    id: UUID
    id_kind: IdDocumentKind
    id_number: str
    full_name: str | None
    phone_number: str | None
    email: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, client: Client) -> ClientResponse:
        return cls(
            id=client.id,
            id_kind=client.id_kind,
            id_number=client.id_number,
            full_name=client.full_name,
            phone_number=client.phone_number,
            email=client.email,
            created_at=client.created_at,
        )


class CaseDocumentResponse(BaseModel):
    id: UUID
    doc_type: str
    source: CaseDocumentSource
    filename: str
    added_at: datetime
    download_url: str
    reading: DocumentReadingResponse | None

    @classmethod
    def from_domain(cls, doc: CaseDocument, *, download_url: str) -> CaseDocumentResponse:
        return cls(
            id=doc.id,
            doc_type=doc.doc_type,
            source=doc.source,
            filename=doc.filename,
            added_at=doc.added_at,
            download_url=download_url,
            reading=DocumentReadingResponse.from_reading(doc.reading) if doc.reading else None,
        )


class CaseFlagResponse(BaseModel):
    id: UUID
    code: str
    message: str
    severity: FlagSeverity
    source_document_id: UUID | None
    raised_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None

    @classmethod
    def from_domain(cls, flag: CaseFlag) -> CaseFlagResponse:
        return cls(**flag.model_dump())


class CaseEventResponse(BaseModel):
    id: UUID
    kind: str
    message: str
    payload: dict[str, Any]
    occurred_at: datetime

    @classmethod
    def from_domain(cls, event: CaseEvent) -> CaseEventResponse:
        return cls(**event.model_dump())


class CaseSummaryResponse(BaseModel):
    """Fila de la lista de expedientes: lo que la consola necesita para el listado."""

    id: UUID
    client_id: UUID
    tax_year: int
    status: CaseStatus
    updated_at: datetime


class PlazoResponse(BaseModel):
    """Cuándo vence esta declaración y cuánto falta.

    VA EN EL EXPEDIENTE Y NO EN UN ENDPOINT APARTE porque es lo primero que hay que saber: antes de
    cuánto se paga, cuándo. Una declaración presentada un día tarde cuesta la sanción mínima
    ($524.000 en 2026, 10 UVT), que es varias veces el precio del producto.

    `dias_restantes` puede ser NEGATIVO y eso no se esconde: significa que el plazo ya pasó y que
    hay sanción corriendo. Recortarlo a cero haría ver una declaración en mora como si estuviera
    al día.
    """

    vence: date
    dias_restantes: int
    vencido: bool
    # Los dos dígitos del documento que determinan la fecha (Decreto 2229 de 2023). Se devuelven
    # para que la pantalla pueda explicar POR QUÉ es esa fecha y no otra: sin eso, el cliente que
    # compara con la fecha de un amigo cree que una de las dos está mal.
    digitos: int


class CaseDetailResponse(BaseModel):
    """El expediente completo: es lo que pinta la vista de detalle en la consola."""

    id: UUID
    tax_year: int
    # `None` cuando el documento del cliente no permite calcularlo (un NIT con dígito de
    # verificación, un pasaporte). No se inventa una fecha: se dice que no se sabe.
    plazo: PlazoResponse | None
    status: CaseStatus
    created_at: datetime
    updated_at: datetime
    client: ClientResponse
    documents: list[CaseDocumentResponse]
    flags: list[CaseFlagResponse]
    open_flags_count: int
    events: list[CaseEventResponse]

    @classmethod
    def from_domain(cls, detail: CaseDetail, *, download_url_builder: Any) -> CaseDetailResponse:
        return cls(
            id=detail.case.id,
            tax_year=detail.case.tax_year,
            plazo=_plazo(detail),
            status=detail.case.status,
            created_at=detail.case.created_at,
            updated_at=detail.case.updated_at,
            client=ClientResponse.from_domain(detail.client),
            documents=[
                CaseDocumentResponse.from_domain(
                    d, download_url=download_url_builder(d.storage_uri, d.filename)
                )
                for d in detail.documents
            ],
            flags=[CaseFlagResponse.from_domain(f) for f in detail.flags],
            open_flags_count=len(detail.open_flags),
            events=[CaseEventResponse.from_domain(e) for e in detail.events],
        )


def _plazo(detail: CaseDetail) -> PlazoResponse | None:
    """El vencimiento de este expediente, o None si el documento no permite calcularlo.

    Se calcula acá y no se guarda: depende de la fecha de hoy (`dias_restantes`), así que un valor
    persistido envejecería en cada consulta. El cálculo es puro y barato.
    """
    try:
        vence = vencimiento_de(detail.client.id_number, detail.case.tax_year)
        digitos = dos_ultimos_digitos(detail.client.id_number)
    except ValidationError:
        # Un pasaporte o un documento con dígito de verificación no permiten leer el par de dígitos
        # del decreto. Decir "no se sabe" es correcto; adivinar una fecha, no.
        return None
    restantes = (vence - date.today()).days
    return PlazoResponse(
        vence=vence, dias_restantes=restantes, vencido=restantes < 0, digitos=digitos
    )
