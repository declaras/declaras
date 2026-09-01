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
    # La declaración ya PRESENTADA del mismo año gravable del expediente. No es lo mismo que
    # PRIOR_RETURN, que es la del año ANTERIOR y sirve de insumo (patrimonio inicial, arrastres):
    # esta es el resultado, y existe para poder comparar contra ella.
    #
    # Solo existe en años ya declarados, así que en el año en curso no está y eso es normal. Su
    # razón de ser es rehacer un año viejo y ver en qué difiere lo que el sistema calcula de lo que
    # se presentó de verdad, que casi siempre es lo que hizo un contador.
    FILED_RETURN = "FILED_RETURN"
    EINVOICE_SUMMARY = "EINVOICE_SUMMARY"
    EVIDENCE = "EVIDENCE"
    CLIENT_DOCUMENT = "CLIENT_DOCUMENT"


# Nombre legible de cada tipo de documento, para los mensajes que ve una persona.
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    DocumentType.RUT: "el RUT",
    DocumentType.EXOGENA: "la información exógena",
    DocumentType.PRIOR_RETURN: "la declaración del año anterior",
    DocumentType.SUGGESTED_RETURN: "el borrador sugerido por la DIAN",
    DocumentType.FILED_RETURN: "la declaración que se presentó ese año",
    DocumentType.EINVOICE_SUMMARY: "el resumen de facturas electrónicas",
    DocumentType.EVIDENCE: "la evidencia de auditoría",
    DocumentType.CLIENT_DOCUMENT: "un documento del cliente",
    # No es un tipo del conector (no lo baja del portal, lo aporta el cliente), pero si
    # tiene lector, asi que aparece en las alertas del expediente y necesita nombre.
    "CERT_INGRESOS_220": "el certificado de ingresos y retenciones",
    # El 210 que Clara dejo escrito en el portal, bajado del portal despues de escribirlo.
    "BORRADOR_ESCRITO": "el borrador que quedó en el portal",
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


class StepState(StrEnum):
    """En que va cada paso de un trabajo.

    `EMPTY` no es una falla: significa que se consulto y no habia nada que traer. A quien
    declara por primera vez la DIAN no le tiene declaracion del anio anterior, y eso es normal.
    Sin este estado, lo normal se reporta como error y asusta sin motivo.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class JobStep(BaseModel):
    """Un paso del trabajo, con nombre en lenguaje de la persona que lo esta esperando."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    state: StepState = StepState.PENDING
    detail: str | None = None

    def as_(self, state: StepState, detail: str | None = None) -> Self:
        return self.model_copy(update={"state": state, "detail": detail})


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
            raise ValueError("El número de documento debe ser solo dígitos.")
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


class DiferenciaDeEscritura(BaseModel):
    """Una casilla que no quedo como se mando: lo enviado y lo que el portal devolvio."""

    casilla: int
    enviado: int | str | None
    leido: int | str | None


class BorradorEscrito(BaseModel):
    """El resultado de escribir el 210 en el portal, con su verificacion.

    LA VERIFICACION NO ES OPCIONAL. Escribir tiene un modo de falla que leer no tiene: un
    payload aceptado con 201 puede quedar guardado DISTINTO de lo que se mando (pasó en el
    primer ensayo real: la codificación corrompió una letra dentro del borrador y el portal
    respondió 201 igual). Por eso el flujo relee el documento completo después del PUT y
    compara casilla por casilla, y este modelo carga las tres listas que un contador necesita
    para confiar o desconfiar del resultado.
    """

    form_id: str
    anio: int
    # Cuantas casillas se mandaron y si TODAS se releyeron identicas.
    escritas: int
    verificado: bool
    # Las que volvieron distintas de lo enviado. Vacia cuando `verificado`.
    diferencias: list[DiferenciaDeEscritura] = Field(default_factory=list)
    # Casillas del cuerpo con valor en el portal que Clara NO calcula (ganancias ocasionales,
    # anticipo del año anterior...). No son un error, pero el contador tiene que verlas: el
    # borrador final es la mezcla de lo nuestro y lo que ya estaba.
    ajenas: dict[int, int | str] = Field(default_factory=dict)
    # El PDF del borrador tal como quedo en el portal, ya guardado en el expediente.
    #
    # ES LA PRUEBA, y por eso vale la pena bajarlo aunque cueste una descarga: la verificacion
    # casilla por casilla dice que el portal guardo lo que se mando, pero eso es nuestro
    # sistema dandose la razon a si mismo. El documento que la DIAN genera es lo que un
    # contador puede mirar, archivar y mostrarle al cliente. `None` si no se pudo bajar, que no
    # invalida la escritura: son dos cosas distintas.
    documento_id: UUID | None = None


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
            raise ValueError(f"No se le puede pedir {names} al conector de la DIAN.")
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
    # En que va el trabajo, paso a paso. Un job de extraccion tarda medio minuto contra el
    # portal real, y sin esto quien espera no sabe si esta funcionando ni en que punto va.
    progress: list[JobStep] = Field(default_factory=list)
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    leased_until: datetime | None = None
    worker_id: str | None = None

    def with_status(self, status: JobStatus, now: datetime) -> Self:
        return self.model_copy(update={"status": status, "updated_at": now})
