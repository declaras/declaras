"""Puertos del dominio.

Las capas externas (Playwright, GCS, SQL) implementan estos protocolos. El dominio y
los servicios solo conocen estas interfaces, de modo que el conector real se puede
sustituir por un doble de prueba sin tocar la logica de orquestacion.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from declaras.domain.models import (
    BorradorEscrito,
    ChallengeAnswer,
    DianCredentials,
    DocumentType,
    IdentityChallenge,
    Job,
    JobKind,
    JobStatus,
    RawDocument,
    StoredDocument,
    TaxpayerRef,
)


@runtime_checkable
class DianSession(Protocol):
    """Sesion contra el portal. Es de un solo uso y hay que cerrarla.

    Un reto de identidad no es una falla sino un estado esperado: si
    `pending_challenge` no es None, la sesion queda parqueada esperando la respuesta
    del contribuyente (patron relevo) y no se puede descargar nada todavia.
    """

    session_id: str

    @property
    def pending_challenge(self) -> IdentityChallenge | None: ...

    async def download(self, doc_type: DocumentType, taxpayer: TaxpayerRef) -> RawDocument:
        """Descarga un documento. Lanza DianDocumentUnavailableError si no existe."""
        ...

    async def capture_evidence(self, label: str) -> RawDocument:
        """Captura de pantalla para auditoria."""
        ...

    async def listar_declaraciones(self) -> list[dict[str, object]]:
        """Los años que la DIAN tiene declarados, con el identificador de cada formulario.

        NO CUESTA UNA PETICION EXTRA: la DIAN responde con el listado completo cuando se le
        pregunta por una declaracion, asi que enumerar el historial es leer lo que ya llego.

        Sirve para dos cosas distintas: ver el historial del contribuyente, y saber que años
        NO declaro, que es donde puede haber un atraso.
        """
        ...

    async def descargar_declaracion(self, anio: int) -> RawDocument:
        """El PDF de la declaracion presentada de un año concreto.

        Se diferencia de `download(PRIOR_RETURN, ...)` en que aquella baja siempre el año
        anterior al del expediente porque lo necesita como insumo; esta baja el año que le
        pidan, que es lo que hace falta para el historial.
        """
        ...

    async def escribir_borrador(
        self, taxpayer: TaxpayerRef, casillas: dict[int, int]
    ) -> BorradorEscrito:
        """Llena el borrador del 210 del contribuyente en el portal y verifica la escritura.

        Es la UNICA operacion del puerto que modifica algo en la cuenta. Lanza
        DianDocumentUnavailableError si no hay borrador editable del año (el mensaje dice
        como crearlo), y el resultado carga la verificacion casilla por casilla.
        """
        ...

    async def answer_challenge(self, answer: ChallengeAnswer) -> None:
        """Responde el reto de identidad y continua el login."""
        ...

    async def close(self) -> None: ...


@runtime_checkable
class DianConnector(Protocol):
    """Fabrica de sesiones autenticadas."""

    async def open_session(
        self, credentials: DianCredentials, taxpayer: TaxpayerRef
    ) -> DianSession:
        """Autentica en el portal y devuelve la sesion.

        Si el portal pide verificacion de identidad, la sesion vuelve con
        `pending_challenge` poblado en lugar de lanzar excepcion.

        Lanza DianInvalidCredentialsError, DianAccountLockedError,
        DianPortalUnavailableError o DianTimeoutError.
        """
        ...


@runtime_checkable
class DocumentStore(Protocol):
    """Almacenamiento de documentos extraidos y evidencias."""

    async def put(
        self, *, taxpayer: TaxpayerRef, document: RawDocument, scope_id: UUID
    ) -> StoredDocument:
        """Almacena un documento.

        `scope_id` agrupa la evidencia de la operacion que produjo el documento: puede ser
        un job de extraccion o un expediente. Se llama asi, y no `job_id`, porque no
        siempre es un job.
        """
        ...

    async def read(self, storage_uri: str) -> bytes: ...

    async def signed_url(self, storage_uri: str, ttl_seconds: int) -> str | None:
        """URL temporal de descarga, o None si el backend no la soporta."""
        ...


@runtime_checkable
class JobRepository(Protocol):
    """Persistencia del ciclo de vida de los jobs."""

    async def create(
        self,
        *,
        kind: JobKind,
        request: dict[str, Any],
        job_id: UUID | None = None,
        progress: list[dict[str, Any]] | None = None,
    ) -> Job:
        """Crea el job ya completo y listo para que un worker lo reclame.

        Acepta el id por adelantado porque hay trabajo que debe quedar hecho ANTES de que el
        job sea reclamable, y ese trabajo necesita el id: la clave de la DIAN vive en una
        boveda llaveada por job, y un worker que reclame un job cuya clave todavia no esta
        guardada falla por credenciales ausentes, que no es el error real.

        El avance inicial entra en el mismo insert por la misma razon: una segunda escritura
        despues del insert es una ventana en la que el job ya es reclamable y aun no esta
        completo.
        """
        ...

    async def get(self, job_id: UUID) -> Job | None: ...

    async def claim_next(self, *, kind: JobKind, worker_id: str, lease_ttl_s: int) -> Job | None:
        """Toma atomicamente el siguiente job encolado y lo marca RUNNING."""
        ...

    async def heartbeat(self, job_id: UUID, *, lease_ttl_s: int) -> None: ...

    async def update_progress(self, job_id: UUID, *, progress: list[dict[str, Any]]) -> None:
        """Publica en que va el trabajo, para que quien espera lo pueda ver."""
        ...

    async def mark_succeeded(self, job_id: UUID, *, result: dict[str, Any]) -> Job: ...

    async def mark_failed(self, job_id: UUID, *, error: dict[str, Any]) -> Job: ...

    async def mark_awaiting_challenge(
        self, job_id: UUID, *, challenge: IdentityChallenge
    ) -> Job: ...

    async def transition(self, job_id: UUID, *, status: JobStatus) -> Job: ...

    async def release_expired_leases(self) -> int:
        """Devuelve a la cola los jobs cuyo worker murio."""
        ...


@runtime_checkable
class LoginAttemptGuard(Protocol):
    """Freno anti bloqueo de cuenta.

    La DIAN bloquea al tercer intento fallido, asi que contamos los fallos por sujeto
    y cortamos antes de llegar a ese punto.
    """

    async def assert_can_attempt(self, subject_key: str) -> None:
        """Lanza DianLoginAttemptsExhaustedError si ya no quedan intentos."""
        ...

    async def register_failure(self, subject_key: str) -> int:
        """Registra un fallo y devuelve los intentos restantes."""
        ...

    async def reset(self, subject_key: str) -> None: ...


@runtime_checkable
class SessionRegistry(Protocol):
    """Sesiones vivas en espera de que el usuario responda un reto de identidad."""

    async def put(self, job_id: UUID, session: DianSession) -> None: ...

    async def get(self, job_id: UUID) -> DianSession | None: ...

    async def discard(self, job_id: UUID) -> None: ...

    async def evict_expired(self) -> int: ...
