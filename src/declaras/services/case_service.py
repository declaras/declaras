"""Servicio del expediente: el que amarra el conector DIAN y la lectura de documentos
con el cliente y su caso.

Es la pieza que hoy no existia y que hace falta para la consola del contador: sin ella,
el conector produce jobs de extraccion sueltos y el servicio de documentos lee archivos
sueltos, pero nada los organiza por cliente ni deja un rastro de lo que paso.
"""

from __future__ import annotations

from uuid import UUID

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
            message=f"Expediente abierto para el año gravable {tax_year}",
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
                "el job de extraccion no esta en estado SUCCEEDED",
                job_id=str(extraction_job.id),
                status=extraction_job.status.value,
            )
        detail = await self._require_detail(case_id)
        result = ExtractionResult.model_validate(extraction_job.result)

        self._assert_same_taxpayer(detail, result)

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

        already_present = {(d.doc_type, d.content_sha256) for d in detail.documents}

        for stored in result.documents:
            if stored.doc_type.value == _EVIDENCE_DOC_TYPE:
                continue  # es evidencia de auditoria, no un insumo del motor
            if (stored.doc_type.value, stored.sha256) in already_present:
                # Otro job ya trajo este mismo documento byte a byte: no se duplica.
                continue

            case_doc = await self._cases.add_document(
                case_id=case_id,
                doc_type=stored.doc_type.value,
                source=CaseDocumentSource.DIAN_PORTAL,
                storage_uri=stored.storage_uri,
                filename=stored.filename,
                content_sha256=stored.sha256,
                extraction_job_id=extraction_job.id,
            )
            await self._cases.add_event(
                case_id=case_id,
                kind="DOCUMENT_LINKED",
                message=(
                    f"Se vinculó {document_label(stored.doc_type.value)} "
                    "desde la consulta a la DIAN"
                ),
                payload={"filename": stored.filename, "job_id": str(extraction_job.id)},
            )
            await self._try_read_and_flag(
                case_id=case_id, case_doc=case_doc, doc_type=stored.doc_type.value
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
            kind="EXTRACTION_LINKED",
            message="Se vinculó la consulta a la DIAN con el expediente",
            payload={
                "job_id": str(extraction_job.id),
                "documents": len(result.documents),
                "failures": len(result.failures),
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
            message=f"El cliente subió {doc_type.replace('_', ' ')}",
            payload={"filename": filename},
        )
        await self._try_read_and_flag(case_id=case_id, case_doc=case_doc, doc_type=doc_type)
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
        self, *, case_id: UUID, case_doc: CaseDocument, doc_type: str
    ) -> None:
        """Intenta leer un documento y convierte sus avisos en flags.

        Si no hay lector para el tipo (documentos aun sin parser, o del cliente que
        esperan el lector por vision), no es un error: simplemente no hay lectura
        estructurada todavia, y el documento queda disponible para revision manual.
        """
        try:
            content = await self._store.read(case_doc.storage_uri)
            reading = self._reader.read(content=content, doc_type=doc_type)
        except UnsupportedDocumentTypeError:
            # Todavia no hay parser para ese tipo: es una limitacion conocida del sistema,
            # no un problema del documento. Queda disponible para revision manual.
            log.info("case.no_reader_yet", case_id=str(case_id), doc_type=doc_type)
            return
        except DocumentUnreadableError as exc:
            # El documento SI deberia poder leerse y no se pudo: el contador debe saberlo,
            # porque significa que hay que volver a pedirlo.
            log.warning("case.document_unreadable", case_id=str(case_id), doc_type=doc_type)
            await self._cases.add_flag(
                case_id=case_id,
                code=exc.code,
                message=f"No se pudo leer {doc_type.replace('_', ' ')}: {exc.message}",
                severity=FlagSeverity.BLOCKING,
                source_document_id=case_doc.id,
            )
            return

        await self._cases.attach_reading(case_doc.id, reading)
        for warning in reading.warnings:
            await self._flag_from_warning(case_id, case_doc.id, warning)
        await self._flag_if_identity_differs(case_id, case_doc, reading)

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
                "la extraccion pertenece a otro contribuyente",
                case_taxpayer=client.subject_key,
                extraction_taxpayer=result.taxpayer.subject_key,
            )
        if result.taxpayer.tax_year != case.tax_year:
            raise TaxpayerMismatchError(
                "la extraccion es de otro anio gravable",
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
                f"El documento está a nombre de {document_id_number}, "
                f"pero el expediente es de {expected}"
            ),
            severity=FlagSeverity.BLOCKING,
            source_document_id=case_doc.id,
        )

    async def _flag_from_warning(
        self, case_id: UUID, document_id: UUID, warning: ReadingWarning
    ) -> None:
        await self._cases.add_flag(
            case_id=case_id,
            code=warning.code,
            message=warning.message,
            severity=FlagSeverity.WARNING,
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
