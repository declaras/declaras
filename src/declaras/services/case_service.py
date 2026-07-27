"""Servicio del expediente: el que amarra el conector DIAN y la lectura de documentos
con el cliente y su caso.

Es la pieza que hoy no existia y que hace falta para la consola del contador: sin ella,
el conector produce jobs de extraccion sueltos y el servicio de documentos lee archivos
sueltos, pero nada los organiza por cliente ni deja un rastro de lo que paso.
"""

from __future__ import annotations

from uuid import UUID

from fastapi.concurrency import run_in_threadpool

from declaras.documents.models import DocumentReading, ReadingWarning
from declaras.documents.service import DocumentReaderService
from declaras.domain.case import (
    Case,
    CaseDetail,
    CaseDocument,
    CaseDocumentSource,
    CaseFlag,
    CaseStatus,
    FlagSeverity,
)
from declaras.domain.case_ports import CaseRepository, ClientRepository
from declaras.domain.errors import (
    CaseNotFoundError,
    DocumentUnreadableError,
    FlagNotFoundError,
    TaxpayerMismatchError,
    UnsupportedDocumentTypeError,
    ValidationError,
)
from declaras.domain.models import (
    DocumentType,
    ExtractionResult,
    IdDocumentKind,
    Job,
    JobStatus,
    RawDocument,
    TaxpayerRef,
    document_label,
)
from declaras.domain.ports import DocumentStore
from declaras.observability import get_logger
from declaras.services.reading_diff import ReadingDiff, compare, describe_sync

log = get_logger(__name__)

_EVIDENCE_DOC_TYPE = "EVIDENCE"


class CaseService:
    def __init__(
        self,
        *,
        clients: ClientRepository,
        cases: CaseRepository,
        store: DocumentStore,
        reader: DocumentReaderService,
    ) -> None:
        self._clients = clients
        self._cases = cases
        self._store = store
        self._reader = reader

    # ─────────────────────────── casos de uso ───────────────────────────

    async def open_case(
        self,
        *,
        id_kind: IdDocumentKind,
        id_number: str,
        tax_year: int,
        full_name: str | None = None,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> CaseDetail:
        """Abre un expediente, creando el cliente si es la primera vez que se ve."""
        client = await self._clients.get_or_create(
            id_kind=id_kind,
            id_number=id_number,
            full_name=full_name,
            phone_number=phone_number,
            email=email,
        )
        case = await self._cases.create(client_id=client.id, tax_year=tax_year)
        await self._cases.add_event(
            case_id=case.id,
            kind="CASE_OPENED",
            message=f"Se abrió la declaración del año gravable {tax_year}",
        )
        log.info("case.opened", case_id=str(case.id), client_id=str(client.id), tax_year=tax_year)
        return await self._require_detail(case.id)

    async def get_detail(self, case_id: UUID) -> CaseDetail:
        return await self._require_detail(case_id)

    async def link_extraction_result(self, *, case_id: UUID, extraction_job: Job) -> CaseDetail:
        """Vuelca el resultado de una extraccion DIAN ya terminada al expediente.

        Cada documento bajado queda registrado, se intenta leer con el servicio de
        documentos (si ya hay un lector para su tipo) y sus avisos se convierten en
        flags. Los documentos que la extraccion no pudo bajar tambien quedan como flags,
        para que el contador sepa que falto sin tener que ir a consultar el job.
        """
        if extraction_job.status is not JobStatus.SUCCEEDED:
            raise ValidationError(
                "La consulta a la DIAN todavía no ha terminado bien.",
                job_id=str(extraction_job.id),
                status=extraction_job.status.value,
            )
        detail = await self._require_detail(case_id)
        result = ExtractionResult.model_validate(extraction_job.result)

        self._assert_same_taxpayer(detail, result)

        # Lo que el expediente ya sabia antes de esta consulta, para poder decir despues si
        # la consulta trajo algo. Se toma antes de reemplazar nada.
        previous_readings = {
            d.doc_type: d.reading
            for d in detail.documents
            if d.source is CaseDocumentSource.DIAN_PORTAL
        }

        # Idempotencia: el agente puede reintentar la llamada (timeout, reenvio). Si este
        # job ya se vinculo, se devuelve el expediente tal como esta en vez de duplicar
        # documentos, flags y eventos.
        if any(d.extraction_job_id == extraction_job.id for d in detail.documents):
            log.info(
                "case.extraction_already_linked",
                case_id=str(case_id),
                job_id=str(extraction_job.id),
            )
            return detail

        diffs: list[ReadingDiff] = []
        reemplazados = 0
        for stored in result.documents:
            if stored.doc_type.value == _EVIDENCE_DOC_TYPE:
                continue  # es evidencia de auditoria, no un insumo del motor

            # Una consulta nueva reemplaza la anterior del mismo tipo. No se puede
            # deduplicar por hash del contenido: la DIAN incrusta la fecha de generacion
            # dentro del archivo, asi que cada descarga del MISMO documento tiene un hash
            # distinto. Y reconsultar es normal (el contador vuelve cuando la DIAN ya
            # publico la exogena), asi que acumular copias dejaria el expediente sin un
            # documento vigente claro.
            reemplazados += await self._supersede_previous(case_id, stored.doc_type.value)

            case_doc = await self._cases.add_document(
                case_id=case_id,
                doc_type=stored.doc_type.value,
                source=CaseDocumentSource.DIAN_PORTAL,
                storage_uri=stored.storage_uri,
                filename=stored.filename,
                content_sha256=stored.sha256,
                extraction_job_id=extraction_job.id,
            )
            reading = await self._try_read_and_flag(
                case_id=case_id,
                case_doc=case_doc,
                doc_type=stored.doc_type.value,
                anio_esperado=detail.case.tax_year,
            )
            diffs.append(
                compare(
                    doc_type=stored.doc_type.value,
                    before=previous_readings.get(stored.doc_type.value),
                    after=reading,
                )
            )

        for failure in result.failures:
            await self._cases.add_flag(
                case_id=case_id,
                code=failure.code,
                message=(
                    f"No se pudo obtener {document_label(failure.doc_type.value)}: "
                    f"{failure.message}"
                ),
                severity=FlagSeverity.WARNING if failure.retryable else FlagSeverity.BLOCKING,
            )

        await self._cases.transition(case_id, status=CaseStatus.READY_FOR_REVIEW)
        await self._cases.add_event(
            case_id=case_id,
            kind="DIAN_QUERY",
            message=describe_sync(diffs),
            payload={
                "job_id": str(extraction_job.id),
                "documents": len(result.documents),
                "failures": len(result.failures),
                "changed": [d.doc_type for d in diffs if d.has_changes],
                # Las copias anteriores se conservan para auditoria; el conteo queda aqui en
                # vez de en un evento por documento.
                "superseded": reemplazados,
            },
        )
        log.info(
            "case.extraction_linked",
            case_id=str(case_id),
            documents=len(result.documents),
            failures=len(result.failures),
        )
        return await self._require_detail(case_id)

    async def add_client_upload(
        self, *, case_id: UUID, doc_type: str, content: bytes, filename: str
    ) -> CaseDetail:
        """El cliente manda un documento por chat: se guarda, se lee si hay lector para
        su tipo, y sus avisos (si los hay) quedan como flags para el contador."""
        detail = await self._require_detail(case_id)
        taxpayer = TaxpayerRef(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            tax_year=detail.case.tax_year,
        )
        raw = RawDocument(doc_type=DocumentType.CLIENT_DOCUMENT, filename=filename, content=content)
        # El almacenamiento agrupa por tipo de documento del conector; los documentos del
        # cliente usan un marcador generico y el nombre real de tipo va en el expediente.
        stored = await self._store.put(taxpayer=taxpayer, document=raw, scope_id=case_id)

        case_doc = await self._cases.add_document(
            case_id=case_id,
            doc_type=doc_type,
            source=CaseDocumentSource.CLIENT_UPLOAD,
            storage_uri=stored.storage_uri,
            filename=filename,
            content_sha256=stored.sha256,
        )
        await self._cases.add_event(
            case_id=case_id,
            kind="DOCUMENT_UPLOADED",
            message=f"Se agregó {document_label(doc_type)}",
            payload={"filename": filename},
        )
        await self._try_read_and_flag(
            case_id=case_id,
            case_doc=case_doc,
            doc_type=doc_type,
            anio_esperado=detail.case.tax_year,
        )
        log.info("case.client_upload", case_id=str(case_id), doc_type=doc_type)
        return await self._require_detail(case_id)

    async def resolve_flag(
        self, *, case_id: UUID, flag_id: UUID, note: str | None = None
    ) -> CaseFlag:
        """Resuelve un flag verificando que pertenezca al expediente indicado.

        Sin esa verificacion, la ruta HTTP permitiria resolver el flag de un expediente
        pasando el id de otro, y la bitacora quedaria contando una historia falsa.
        """
        detail = await self._require_detail(case_id)
        if flag_id not in {f.id for f in detail.flags}:
            raise FlagNotFoundError(flag_id=str(flag_id), case_id=str(case_id))
        flag = await self._cases.resolve_flag(flag_id, note=note)
        await self._cases.add_event(
            case_id=case_id,
            kind="FLAG_RESOLVED",
            message=f"Se marcó como revisado: {flag.message}",
            payload={"flag_id": str(flag_id), "note": note},
        )
        return flag

    # ─────────────────────────── internos ───────────────────────────

    async def _try_read_and_flag(
        self, *, case_id: UUID, case_doc: CaseDocument, doc_type: str, anio_esperado: int
    ) -> DocumentReading | None:
        """Intenta leer un documento y convierte sus avisos en flags.

        Si no hay lector para el tipo (documentos aun sin parser, o del cliente que
        esperan el lector por vision), no es un error: simplemente no hay lectura
        estructurada todavia, y el documento queda disponible para revision manual.

        El anio gravable del caso baja hasta el lector: para los certificados que lee un modelo
        es el guard del documento del anio equivocado, que es el error mas comun, y sin bajarlo
        no dispararia nunca por este camino.
        """
        try:
            content = await self._store.read(case_doc.storage_uri)
            # Al threadpool y no en el loop: leer es bloqueante, y un lector con modelo tarda
            # decenas de segundos. En el loop congelaria todas las demas requests y el worker
            # de extracciones, que corre como task del mismo loop.
            reading = await run_in_threadpool(
                self._reader.read,
                content=content,
                doc_type=doc_type,
                anio_esperado=anio_esperado,
            )
        except UnsupportedDocumentTypeError:
            # Todavia no hay parser para ese tipo: es una limitacion conocida del sistema,
            # no un problema del documento. Queda disponible para revision manual.
            log.info("case.no_reader_yet", case_id=str(case_id), doc_type=doc_type)
            return None
        except DocumentUnreadableError as exc:
            # El documento SI deberia poder leerse y no se pudo: el contador debe saberlo,
            # porque significa que hay que volver a pedirlo.
            log.warning("case.document_unreadable", case_id=str(case_id), doc_type=doc_type)
            await self._cases.add_flag(
                case_id=case_id,
                code=exc.code,
                message=f"No se pudo leer {document_label(doc_type)}: {exc.message}",
                severity=FlagSeverity.BLOCKING,
                source_document_id=case_doc.id,
            )
            return None

        await self._cases.attach_reading(case_doc.id, reading)
        for warning in reading.warnings:
            await self._flag_from_warning(case_id, case_doc.id, warning)
        await self._flag_if_identity_differs(case_id, case_doc, reading)
        return reading

    async def _supersede_previous(self, case_id: UUID, doc_type: str) -> int:
        """Marca reemplazados los documentos del portal de ese tipo y cierra sus avisos.

        El documento viejo se conserva (la DIAN puede preguntar hasta tres anios despues),
        pero sus flags se resuelven solos: un aviso sobre un documento que ya no es el
        vigente solo ensucia la lista de pendientes.

        No registra un evento por documento. Una consulta reemplaza los cinco a la vez, y cinco
        lineas seguidas diciendo lo mismo no son la historia del expediente, son ruido que tapa
        lo que si paso; el conteo va en el evento unico de la consulta.
        """
        reemplazados = await self._cases.supersede_documents(
            case_id=case_id, doc_type=doc_type, source=CaseDocumentSource.DIAN_PORTAL
        )
        if not reemplazados:
            return 0

        detail = await self._require_detail(case_id)
        ids_reemplazados = {d.id for d in reemplazados}
        for flag in detail.open_flags:
            if flag.source_document_id in ids_reemplazados:
                await self._cases.resolve_flag(
                    flag.id, note="El documento se reemplazó por una consulta más reciente."
                )

        log.info("case.document_superseded", case_id=str(case_id), doc_type=doc_type)
        return len(reemplazados)

    def _assert_same_taxpayer(self, detail: CaseDetail, result: ExtractionResult) -> None:
        """El expediente y la extraccion tienen que ser de la misma persona y anio.

        Es la proteccion mas importante del expediente: sin esta verificacion, un job de
        extraccion de otro contribuyente se puede vincular a un expediente ajeno y mezclar
        informacion tributaria de dos personas, que es un dano grave y casi imposible de
        detectar despues.
        """
        client, case = detail.client, detail.case
        if (
            result.taxpayer.id_kind != client.id_kind
            or result.taxpayer.id_number != client.id_number
        ):
            raise TaxpayerMismatchError(
                "La consulta pertenece a otra persona.",
                case_taxpayer=client.subject_key,
                extraction_taxpayer=result.taxpayer.subject_key,
            )
        if result.taxpayer.tax_year != case.tax_year:
            raise TaxpayerMismatchError(
                "La consulta es de otro año gravable.",
                case_tax_year=case.tax_year,
                extraction_tax_year=result.taxpayer.tax_year,
            )

    async def _flag_if_identity_differs(
        self, case_id: UUID, case_doc: CaseDocument, reading: DocumentReading
    ) -> None:
        """Detecta un documento que no es del titular del expediente.

        Casi todos los documentos del portal traen el numero de identificacion del
        contribuyente. Si el que trae el documento no es el del cliente, lo mas probable es
        que se haya subido el certificado de otra persona, y eso tiene que frenar el
        expediente antes de que ese valor entre al calculo.
        """
        detail = await self._require_detail(case_id)
        document_id_number = reading.field("id_number")
        if not document_id_number:
            return
        expected = detail.client.id_number
        if str(document_id_number).strip() == expected:
            return
        await self._cases.add_flag(
            case_id=case_id,
            code="DOCUMENT_IDENTITY_MISMATCH",
            message=(
                f"Este documento está a nombre de la identificación {document_id_number}, "
                f"y la declaración es de la {expected}. Ese valor no puede entrar al cálculo."
            ),
            severity=FlagSeverity.BLOCKING,
            source_document_id=case_doc.id,
        )

    async def _flag_from_warning(
        self, case_id: UUID, document_id: UUID, warning: ReadingWarning
    ) -> None:
        """Convierte un aviso de lectura en un pendiente del expediente.

        Un aviso que no le pide nada a nadie queda como constancia (`INFO`) y no como algo por
        atender: mezclarlo con los pendientes reales les quita autoridad, y a la larga hace que
        la lista se deje de mirar.
        """
        await self._cases.add_flag(
            case_id=case_id,
            code=warning.code,
            message=warning.message,
            severity=FlagSeverity.WARNING if warning.needs_action else FlagSeverity.INFO,
            source_document_id=document_id,
        )

    async def _require_case(self, case_id: UUID) -> Case:
        case = await self._cases.get(case_id)
        if case is None:
            raise CaseNotFoundError(case_id=str(case_id))
        return case

    async def _require_detail(self, case_id: UUID) -> CaseDetail:
        detail = await self._cases.get_detail(case_id)
        if detail is None:
            raise CaseNotFoundError(case_id=str(case_id))
        return detail
