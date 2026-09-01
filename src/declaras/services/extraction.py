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
from uuid import UUID, uuid4

from declaras.config import Settings
from declaras.domain.errors import (
    DeclarasError,
    DianDocumentUnavailableError,
    DianError,
    DianSessionExpiredError,
    JobNotFoundError,
    JobStateConflictError,
)
from declaras.domain.models import (
    ChallengeAnswer,
    DianCredentials,
    DocumentFailure,
    DocumentType,
    ExtractionRequest,
    ExtractionResult,
    Job,
    JobKind,
    JobStatus,
    JobStep,
    StepState,
    StoredDocument,
    document_label,
)
from declaras.domain.ports import (
    DianConnector,
    DianSession,
    DocumentStore,
    JobRepository,
    LoginAttemptGuard,
)
from declaras.observability import get_logger
from declaras.services.apertura import abrir_sesion_con_freno
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
        """Guarda la clave y crea el job ya completo. Falla rapido si no hay intentos.

        EL ORDEN IMPORTA Y NO ES OBVIO: el insert del job lo deja en cola, y desde ese
        instante cualquier worker puede reclamarlo. Si la clave todavia no esta en la boveda,
        el worker falla con "sesion expirada" en vez de intentar el login, y ese camino no
        cuenta el intento fallido contra el bloqueo de la cuenta.
        """
        await self._guard.assert_can_attempt(request.taxpayer.subject_key)

        # El plan se publica al encolar, no al empezar a trabajar: los pasos se conocen desde
        # que se hace la peticion, y asi la pantalla los pinta completos desde el primer
        # instante en vez de irlos haciendo aparecer. Va en el mismo insert para que el job no
        # exista a medias.
        pasos = _plan_de_trabajo(request)
        job_id = uuid4()
        await self._vault.put(job_id, credentials)
        try:
            job = await self._jobs.create(
                kind=JobKind.DIAN_EXTRACTION,
                request=request.model_dump(mode="json"),
                job_id=job_id,
                progress=[paso.model_dump(mode="json") for paso in pasos],
            )
        except Exception:
            # Sin job que la consuma, la clave no puede quedarse en memoria.
            await self._vault.discard(job_id)
            raise

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
                "El trabajo no está esperando una verificación.", status=job.status.value
            )

        session = await self._registry.get(job_id)
        if session is None:
            error = DianSessionExpiredError(
                "La sesión con la DIAN se venció antes de recibir la respuesta, "
                "así que hay que empezar de nuevo."
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
        # El plan ya se publico al encolar; al retomar un job parqueado se conserva lo andado.
        pasos = list(job.progress) or _plan_de_trabajo(request)

        try:
            session = await self._resume_or_login(job, request, pasos)
            if session is None:
                return  # parqueado esperando al contribuyente
            result = await self._collect(job, request, session, pasos)
        except DeclarasError as exc:
            await self._publicar(job.id, _marcar_fallido(pasos, exc.message))
            await self._fail(job, exc)
            return
        except Exception as exc:  # pragma: no cover - red de seguridad
            log.exception("extraction.unexpected_error", job_id=str(job.id))
            await self._publicar(job.id, _marcar_fallido(pasos, "algo salió mal"))
            await self._fail(job, DeclarasError(str(exc)[:200]))
            return

        result.started_at = started_at
        await self._succeed(job, request, result)

    # ─────────────────────────── pasos internos ───────────────────────────

    async def _resume_or_login(
        self, job: Job, request: ExtractionRequest, pasos: list[JobStep]
    ) -> DianSession | None:
        """Reusa la sesion parqueada si existe; si no, autentica desde cero."""
        session = await self._registry.get(job.id)
        if session is not None:
            if session.pending_challenge is not None:
                return None
            log.info("extraction.session_resumed", job_id=str(job.id))
            _avanzar(pasos, _PASO_ENTRAR, StepState.DONE)
            await self._publicar(job.id, pasos)
            return session

        _avanzar(pasos, _PASO_ENTRAR, StepState.RUNNING)
        await self._publicar(job.id, pasos)

        credentials = await self._vault.get(job.id)
        if credentials is None:
            raise DianSessionExpiredError(
                "La clave ya no está en memoria, así que hay que empezar la consulta de nuevo."
            )

        session = await abrir_sesion_con_freno(
            connector=self._connector,
            guard=self._guard,
            credentials=credentials,
            titular=request.taxpayer,
            motivo="extraccion",
        )

        # Toda sesion queda registrada, no solo las que esperan reto: asi _cleanup
        # siempre encuentra el navegador y no quedan contextos huerfanos.
        await self._registry.put(job.id, session)

        if session.pending_challenge is not None:
            _avanzar(pasos, _PASO_ENTRAR, StepState.RUNNING, "la DIAN pidió verificar tu identidad")
            await self._publicar(job.id, pasos)
            await self._jobs.mark_awaiting_challenge(job.id, challenge=session.pending_challenge)
            log.info("extraction.awaiting_challenge", job_id=str(job.id))
            return None

        _avanzar(pasos, _PASO_ENTRAR, StepState.DONE)
        await self._publicar(job.id, pasos)
        return session

    async def _collect(
        self, job: Job, request: ExtractionRequest, session: DianSession, pasos: list[JobStep]
    ) -> ExtractionResult:
        """Descarga y almacena cada documento. Una falla no cancela las demas."""
        documents: list[StoredDocument] = []
        failures: list[DocumentFailure] = []

        await self._capture_evidence(job, request, session, label="post-login")

        for doc_type in request.doc_types:
            _avanzar(pasos, doc_type.value, StepState.RUNNING)
            await self._publicar(job.id, pasos)
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
                # Que la DIAN no tenga un documento no es una falla del proceso: le pasa a quien
                # declara por primera vez, y marcarlo en rojo asusta sin motivo.
                estado = (
                    StepState.EMPTY
                    if isinstance(exc, DianDocumentUnavailableError)
                    else StepState.FAILED
                )
                _avanzar(pasos, doc_type.value, estado, exc.message)
                await self._publicar(job.id, pasos)
                if isinstance(exc, DianSessionExpiredError):
                    raise
            else:
                _avanzar(pasos, doc_type.value, StepState.DONE)
                await self._publicar(job.id, pasos)

        documents += await self._historial(job, request, session, pasos)

        if not documents and failures:
            # Nada se logro: es una falla del job, no una extraccion parcial.
            first = failures[0]
            raise DianError(f"No se pudo traer ningún documento de la DIAN ({first.code}).")

        now = datetime.now(UTC)
        return ExtractionResult(
            taxpayer=request.taxpayer,
            documents=documents,
            failures=failures,
            started_at=now,
            finished_at=now,
        )

    async def _historial(
        self, job: Job, request: ExtractionRequest, session: DianSession, pasos: list[JobStep]
    ) -> list[StoredDocument]:
        """Las declaraciones de años anteriores, en la MISMA sesion que ya esta abierta.

        ═══ POR QUE VA AQUI Y NO EN UN BOTON APARTE ═══

        Vivio un rato detras de un boton ("Revisar en la DIAN") y eso estaba mal por dos
        razones. La primera es de producto: es un paso manual con clave otra vez, y este
        sistema existe para quitar pasos manuales. La segunda es la que decide: la sesion ya
        esta abierta, y lo escaso NO es la descarga sino el LOGIN —la DIAN bloquea la cuenta al
        tercer intento fallido—, asi que pedir la clave de nuevo gasta el recurso caro para
        ahorrarse el barato.

        ═══ DOS AÑOS, NO CINCO ═══

        Aca van los dos ultimos, que es lo que se usa: el año anterior es insumo del calculo
        (patrimonio inicial, anticipos, saldos a favor) y el de antes sirve para ver si el
        patrimonio cuadra en serie. Los cinco siguen disponibles en el boton del historial, que
        ahora es lo que su nombre dice: revisar mas atras cuando alguien quiere.

        Traer cinco PDF en cada consulta seria alargar el camino critico —el contador esperando
        la pantalla— por documentos que casi nadie abre.

        UNA FALLA AQUI NO TUMBA LA EXTRACCION. Lo que importa ya se trajo; que la DIAN no tenga
        un año viejo, o que este conector no sepa listarlos, no puede convertir una extraccion
        buena en una fallida.
        """
        # PEDIR UN SUBCONJUNTO ES PEDIR ESO Y NADA MAS. Si quien llama enumero los documentos
        # que quiere, agregarle el historial por iniciativa propia le cambia el contrato: se
        # trae solo cuando pidio la declaracion anterior, que es de lo que el historial es la
        # continuacion natural.
        if DocumentType.PRIOR_RETURN not in request.doc_types:
            return []

        anterior = request.taxpayer.tax_year - 1
        try:
            declaraciones = await session.listar_declaraciones()
        except Exception as exc:
            # El conector de navegador no sabe listar (la operacion vive en el HTTP), y la DIAN
            # puede no tener ninguna. Las dos son normales y se registran sin alarma.
            log.info(
                "extraction.historial_no_disponible",
                job_id=str(job.id),
                motivo=str(exc)[:120],
            )
            _avanzar(pasos, _PASO_HISTORIAL, StepState.EMPTY, "no se pudieron consultar")
            await self._publicar(job.id, pasos)
            return []

        anios = sorted(
            (int(d["anio"]) for d in declaraciones if int(d["anio"]) <= anterior), reverse=True
        )[:_ANIOS_DE_HISTORIAL]
        _avanzar(pasos, _PASO_HISTORIAL, StepState.RUNNING)
        await self._publicar(job.id, pasos)

        traidos: list[StoredDocument] = []
        for anio in anios:
            try:
                raw = await session.descargar_declaracion(anio)
                stored = await self._store.put(
                    taxpayer=request.taxpayer, document=raw, scope_id=job.id
                )
            except DianError as exc:
                log.warning(
                    "extraction.historial_anio_fallido",
                    job_id=str(job.id),
                    anio=anio,
                    code=exc.code,
                )
                continue
            traidos.append(stored)

        if traidos:
            cuantas = len(traidos)
            _avanzar(
                pasos,
                _PASO_HISTORIAL,
                StepState.DONE,
                f"{cuantas} {'declaración' if cuantas == 1 else 'declaraciones'}",
            )
        else:
            # Vacio no es fallido: quien declara por primera vez no tiene años anteriores, y
            # pintarlo en rojo asusta sin motivo.
            _avanzar(pasos, _PASO_HISTORIAL, StepState.EMPTY, "no hay declaraciones anteriores")
        await self._publicar(job.id, pasos)
        return traidos

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

    async def _publicar(self, job_id: UUID, pasos: list[JobStep]) -> None:
        """Deja escrito en que va el trabajo.

        Publicar el avance tambien renueva el lease del worker, asi que reemplaza al latido que
        habia antes: mientras el trabajo reporta que avanza, no hay razon para que otro lo
        reclame. Nunca tumba el job: si no se puede escribir el avance, el trabajo sigue.
        """
        try:
            await self._jobs.update_progress(
                job_id, progress=[paso.model_dump(mode="json") for paso in pasos]
            )
        except Exception as exc:  # pragma: no cover - el avance nunca tumba el job
            log.warning("extraction.progress_failed", job_id=str(job_id), error=str(exc)[:160])

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


# ─────────────────────────── el plan de trabajo ───────────────────────────
#
# Una extraccion contra el portal real tarda cerca de medio minuto. Sin decir en que va, quien
# la lanza mira una pantalla quieta y no sabe si esta funcionando, si la clave estaba bien o si
# se colgo. Los pasos se nombran como los entiende quien espera, no como se llaman adentro.

_PASO_ENTRAR = "login"
# Cuantos años atras trae la extraccion sola. El boton del historial llega mas lejos.
_ANIOS_DE_HISTORIAL = 2
_PASO_HISTORIAL = "historial"
_ETIQUETAS = {
    DocumentType.RUT: "Tu RUT",
    DocumentType.EXOGENA: "Lo que otros reportaron a tu nombre",
    DocumentType.EINVOICE_SUMMARY: "Tus facturas electrónicas",
    DocumentType.PRIOR_RETURN: "Tu declaración del año pasado",
    DocumentType.SUGGESTED_RETURN: "El borrador que la DIAN te preparó",
}


def _plan_de_trabajo(request: ExtractionRequest) -> list[JobStep]:
    """Los pasos que va a dar el trabajo, todos visibles desde el principio.

    Se declaran completos y en pendiente antes de empezar, no se van agregando: quien espera
    tiene que poder ver cuanto falta, y una lista que crece sola no dice cuanto falta.
    """
    pasos = [JobStep(key=_PASO_ENTRAR, label="Entrar al portal de la DIAN")]
    pasos += [
        JobStep(key=doc.value, label=_ETIQUETAS.get(doc, document_label(doc.value).capitalize()))
        for doc in request.doc_types
    ]
    # El historial es UN paso y no uno por año, justamente por la regla de arriba: cuantos años
    # tiene la persona solo se sabe despues de preguntarle a la DIAN, asi que un paso por año
    # seria una lista que crece sola mientras alguien la mira.
    if DocumentType.PRIOR_RETURN in request.doc_types:
        pasos.append(JobStep(key=_PASO_HISTORIAL, label="Tus declaraciones de años anteriores"))
    return pasos


def _avanzar(
    pasos: list[JobStep], key: str, state: StepState, detail: str | None = None
) -> list[JobStep]:
    for indice, paso in enumerate(pasos):
        if paso.key == key:
            pasos[indice] = paso.as_(state, detail)
            break
    return pasos


def _marcar_fallido(pasos: list[JobStep], detail: str) -> list[JobStep]:
    """Lo que estaba en curso cuando se cayo el trabajo es lo que fallo."""
    for indice, paso in enumerate(pasos):
        if paso.state is StepState.RUNNING:
            pasos[indice] = paso.as_(StepState.FAILED, detail)
    return pasos
