"""Orquestacion de la extraccion: es el caso de uso central del conector.

Reglas que se hacen cumplir aca y no en los adaptadores:
  1. Antes de intentar login se consulta la guarda anti bloqueo de cuenta.
  2. Una clave rechazada consume un intento y se reporta con los intentos restantes.
  3. Un reto de identidad parquea la sesion viva y deja el job AWAITING_CHALLENGE.
  4. Un documento que falla no tumba la extraccion: se reporta como falla parcial.
  5. Las credenciales se destruyen al terminar, pase lo que pase.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from declaras.config import Settings
from declaras.domain.errors import (
    DeclarasError,
    DianError,
    DianInvalidCredentialsError,
    DianSessionExpiredError,
    JobNotFoundError,
    JobStateConflictError,
)
from declaras.domain.models import (
    ChallengeAnswer,
    DianCredentials,
    DocumentFailure,
    ExtractionRequest,
    ExtractionResult,
    Job,
    JobKind,
    JobStatus,
    StoredDocument,
)
from declaras.domain.ports import (
    DianConnector,
    DianSession,
    DocumentStore,
    JobRepository,
    LoginAttemptGuard,
)
from declaras.observability import get_logger
from declaras.services.credential_vault import InMemoryCredentialVault
from declaras.services.notifier import WebhookNotifier
from declaras.services.session_registry import InMemorySessionRegistry

log = get_logger(__name__)


class ExtractionService:
    def __init__(
        self,
        *,
        connector: DianConnector,
        store: DocumentStore,
        jobs: JobRepository,
        guard: LoginAttemptGuard,
        vault: InMemoryCredentialVault,
        registry: InMemorySessionRegistry,
        notifier: WebhookNotifier,
        settings: Settings,
    ) -> None:
        self._connector = connector
        self._store = store
        self._jobs = jobs
        self._guard = guard
        self._vault = vault
        self._registry = registry
        self._notifier = notifier
        self._settings = settings

    # ─────────────────────────── casos de uso ───────────────────────────

    async def enqueue(self, request: ExtractionRequest, credentials: DianCredentials) -> Job:
        """Crea el job y guarda la clave en memoria. Falla rapido si no hay intentos."""
        await self._guard.assert_can_attempt(request.taxpayer.subject_key)
        job = await self._jobs.create(
            kind=JobKind.DIAN_EXTRACTION, request=request.model_dump(mode="json")
        )
        await self._vault.put(job.id, credentials)
        log.info(
            "extraction.enqueued",
            job_id=str(job.id),
            tax_year=request.taxpayer.tax_year,
            doc_types=[d.value for d in request.doc_types],
        )
        return job

    async def submit_challenge_answer(self, job_id: UUID, answer: ChallengeAnswer) -> Job:
        """Recibe la respuesta del contribuyente y reencola el job para continuar."""
        job = await self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id=str(job_id))
        if job.status is not JobStatus.AWAITING_CHALLENGE:
            raise JobStateConflictError(
                "el job no esta esperando verificacion", status=job.status.value
            )

        session = await self._registry.get(job_id)
        if session is None:
            error = DianSessionExpiredError(
                "la sesion expiro antes de recibir la respuesta; hay que reiniciar"
            )
            await self._jobs.mark_failed(job_id, error=error.to_payload())
            raise error

        await session.answer_challenge(answer)
        if session.pending_challenge is not None:
            return job
        log.info("extraction.challenge_resolved", job_id=str(job_id))
        return await self._jobs.transition(job_id, status=JobStatus.QUEUED)

    async def run(self, job: Job) -> None:
        """Ejecuta o retoma un job. La invoca el worker."""
        request = ExtractionRequest.model_validate(job.request)
        started_at = datetime.now(UTC)

        try:
            session = await self._resume_or_login(job, request)
            if session is None:
                return  # parqueado esperando al contribuyente
            result = await self._collect(job, request, session)
        except DeclarasError as exc:
            await self._fail(job, exc)
            return
        except Exception as exc:  # pragma: no cover - red de seguridad
            log.exception("extraction.unexpected_error", job_id=str(job.id))
            await self._fail(job, DeclarasError(str(exc)[:200]))
            return

        result.started_at = started_at
        await self._succeed(job, request, result)

    # ─────────────────────────── pasos internos ───────────────────────────

    async def _resume_or_login(self, job: Job, request: ExtractionRequest) -> DianSession | None:
        """Reusa la sesion parqueada si existe; si no, autentica desde cero."""
        session = await self._registry.get(job.id)
        if session is not None:
            if session.pending_challenge is not None:
                return None
            log.info("extraction.session_resumed", job_id=str(job.id))
            return session

        credentials = await self._vault.get(job.id)
        if credentials is None:
            raise DianSessionExpiredError(
                "las credenciales del job expiraron; el usuario debe reiniciar el proceso"
            )

        subject = request.taxpayer.subject_key
        await self._guard.assert_can_attempt(subject)
        try:
            session = await self._connector.open_session(credentials, request.taxpayer)
        except DianInvalidCredentialsError as exc:
            remaining = await self._guard.register_failure(subject)
            raise DianInvalidCredentialsError(
                exc.message, attempts_remaining=remaining, **exc.details
            ) from exc

        await self._guard.reset(subject)

        # Toda sesion queda registrada, no solo las que esperan reto: asi _cleanup
        # siempre encuentra el navegador y no quedan contextos huerfanos.
        await self._registry.put(job.id, session)

        if session.pending_challenge is not None:
            await self._jobs.mark_awaiting_challenge(job.id, challenge=session.pending_challenge)
            log.info("extraction.awaiting_challenge", job_id=str(job.id))
            return None
        return session

    async def _collect(
        self, job: Job, request: ExtractionRequest, session: DianSession
    ) -> ExtractionResult:
        """Descarga y almacena cada documento. Una falla no cancela las demas."""
        documents: list[StoredDocument] = []
        failures: list[DocumentFailure] = []

        await self._capture_evidence(job, request, session, label="post-login")

        for doc_type in request.doc_types:
            try:
                raw = await session.download(doc_type, request.taxpayer)
                stored = await self._store.put(
                    taxpayer=request.taxpayer, document=raw, scope_id=job.id
                )
                documents.append(stored)
            except DianError as exc:
                log.warning(
                    "extraction.document_failed",
                    job_id=str(job.id),
                    doc_type=doc_type.value,
                    code=exc.code,
                )
                failures.append(
                    DocumentFailure(
                        doc_type=doc_type,
                        code=exc.code,
                        message=exc.message,
                        retryable=exc.retryable,
                    )
                )
                await self._heartbeat(job.id)
                if isinstance(exc, DianSessionExpiredError):
                    raise
            else:
                await self._heartbeat(job.id)

        if not documents and failures:
            # Nada se logro: es una falla del job, no una extraccion parcial.
            first = failures[0]
            raise DianError(f"ningun documento pudo descargarse ({first.code})")

        now = datetime.now(UTC)
        return ExtractionResult(
            taxpayer=request.taxpayer,
            documents=documents,
            failures=failures,
            started_at=now,
            finished_at=now,
        )

    async def _capture_evidence(
        self, job: Job, request: ExtractionRequest, session: DianSession, *, label: str
    ) -> None:
        if not self._settings.dian_capture_evidence:
            return
        try:
            evidence = await session.capture_evidence(label)
            await self._store.put(taxpayer=request.taxpayer, document=evidence, scope_id=job.id)
        except Exception as exc:  # pragma: no cover - la evidencia nunca tumba el job
            log.warning("extraction.evidence_failed", job_id=str(job.id), error=str(exc)[:160])

    async def _succeed(
        self, job: Job, request: ExtractionRequest, result: ExtractionResult
    ) -> None:
        await self._cleanup(job.id, keep_credentials=False)
        updated = await self._jobs.mark_succeeded(job.id, result=result.model_dump(mode="json"))
        log.info(
            "extraction.succeeded",
            job_id=str(job.id),
            documents=len(result.documents),
            failures=len(result.failures),
        )
        await self._maybe_notify(request, updated)

    async def _fail(self, job: Job, error: DeclarasError) -> None:
        # Si el job se va a reintentar, la clave debe sobrevivir: sin ella el reintento
        # solo puede fallar por credenciales ausentes, que no es el error real.
        will_retry = error.retryable and job.attempts < self._settings.worker_max_attempts
        await self._cleanup(job.id, keep_credentials=will_retry)
        updated = await self._jobs.mark_failed(job.id, error=error.to_payload())
        log.warning("extraction.failed", job_id=str(job.id), code=error.code, will_retry=will_retry)
        request = ExtractionRequest.model_validate(job.request)
        await self._maybe_notify(request, updated)

    async def _cleanup(self, job_id: UUID, *, keep_credentials: bool) -> None:
        """Cierra la sesion del navegador y, salvo que haya reintento, borra la clave.

        La sesion siempre se cierra: si el job fallo, ese navegador ya no sirve. La
        clave solo se conserva cuando queda un reintento pendiente.
        """
        session = await self._registry.get(job_id)
        if session is not None:
            try:
                await session.close()
            except Exception as exc:  # pragma: no cover
                log.warning("extraction.session_close_failed", error=str(exc)[:160])
        await self._registry.discard(job_id)
        if not keep_credentials:
            await self._vault.discard(job_id)

    async def _heartbeat(self, job_id: UUID) -> None:
        await self._jobs.heartbeat(job_id, lease_ttl_s=self._settings.worker_lease_ttl_s)

    async def _maybe_notify(self, request: ExtractionRequest, job: Job) -> None:
        if not request.callback_url:
            return
        await self._notifier.notify(
            request.callback_url,
            {
                "job_id": str(job.id),
                "status": job.status.value,
                "result": job.result,
                "error": job.error,
            },
        )
