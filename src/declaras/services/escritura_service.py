"""Escribir el 210 calculado en el portal de la DIAN, con las compuertas del producto.

El adaptador sabe COMO escribir (la API, la codificacion, la relectura); este servicio
decide CUANDO se puede y deja el rastro. Son responsabilidades distintas a proposito: el
adaptador no conoce expedientes ni estados, y este servicio no conoce la forma del payload.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from declaras.domain.case import CaseDocumentSource, CaseStatus
from declaras.domain.case_ports import CaseRepository
from declaras.domain.errors import CaseNotFoundError, DeclarasError, DianError
from declaras.domain.models import (
    BorradorEscrito,
    DianCredentials,
    DocumentType,
    TaxpayerRef,
)
from declaras.domain.ports import DianConnector, DocumentStore, LoginAttemptGuard
from declaras.observability import get_logger
from declaras.services.apertura import abrir_sesion_con_freno
from declaras.services.conciliacion_service import ConciliacionService

log = get_logger(__name__)

# Como se llama en el expediente el 210 que Clara dejo en el portal. Tipo propio y no
# `SUGGESTED_RETURN`: aquel es el borrador que la DIAN precarga sola, y son dos documentos
# distintos que vale la pena poder comparar.
_DOC_BORRADOR = "BORRADOR_ESCRITO"


class BorradorNoCerradoError(DeclarasError):
    """El borrador no esta dado por listo: escribirlo seria publicar cifras sin revisar.

    Es la misma disciplina del cierre, extendida al portal: lo que sale de Clara hacia la
    cuenta real de un contribuyente tiene que haber pasado por el "dar por buena" del
    contador. Un 210 a medio decidir en el Muisca es peor que ninguno, porque nada en el
    portal dice que esta incompleto.
    """

    code = "BORRADOR_NO_CERRADO"
    http_status = 409
    default_message = (
        "El borrador tiene que estar dado por listo antes de escribirlo en el portal. "
        "Revisa y cierra el borrador primero."
    )


class SinClaveError(DeclarasError):
    """No llego clave y no hay ninguna guardada para ese cliente."""

    code = "SIN_CLAVE_DIAN"
    http_status = 400
    default_message = (
        "Hace falta la clave del portal de la DIAN: no hay ninguna guardada para este cliente."
    )


class EscrituraService:
    def __init__(
        self,
        *,
        connector: DianConnector,
        cases: CaseRepository,
        conciliacion: ConciliacionService,
        guard: LoginAttemptGuard,
        store: DocumentStore,
        clave: object,
    ) -> None:
        self._connector = connector
        self._cases = cases
        self._conciliacion = conciliacion
        self._guard = guard
        self._store = store
        self._clave = clave

    async def escribir(
        self, case_id: UUID, password: SecretStr | None = None
    ) -> BorradorEscrito:
        """Lleva el 210 del expediente al borrador del portal y verifica lo guardado.

        LA CLAVE PUEDE VENIR O ESTAR GUARDADA. Preparar una declaracion son varias visitas al
        portal repartidas en dias, y quien opera la consola no tiene la clave del cliente:
        pedirla en cada paso significaba una llamada por paso. Si llega en la peticion se usa
        y se guarda (cifrada) para los siguientes; si no llega, se usa la guardada.

        La cedula NO viaja en la peticion: sale del expediente, porque aceptar otra seria
        escribir el 210 de una persona con la sesion de otra.
        """
        detail = await self._cases.get_detail(case_id)
        if detail is None:
            raise CaseNotFoundError(case_id=str(case_id))
        clave = password or await self._clave.recuperar(detail.client.id)
        if clave is None:
            raise SinClaveError()
        if detail.case.status is not CaseStatus.DRAFT_READY:
            raise BorradorNoCerradoError()
        credentials = DianCredentials(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            password=clave,
        )

        # Las casillas se calculan ANTES de abrir la sesion: si el caso no se puede armar,
        # el 409 sale sin haber gastado un login del portal (la DIAN bloquea la cuenta a
        # los pocos intentos, asi que cada sesion cuenta).
        casillas = {c.numero: c.valor for c in await self._conciliacion.formulario(case_id)}

        titular = TaxpayerRef(
            id_kind=detail.client.id_kind,
            id_number=detail.client.id_number,
            tax_year=detail.case.tax_year,
        )
        # Con freno, igual que la extraccion: escribir el borrador tambien empieza por un
        # login, y un login fallido cuenta para el bloqueo de la cuenta.
        session = await abrir_sesion_con_freno(
            connector=self._connector,
            guard=self._guard,
            credentials=credentials,
            titular=titular,
            motivo="escritura_borrador",
        )
        try:
            if session.pending_challenge is not None:
                # El relevo de identidad vive en el flujo de extraccion. Antes que duplicarlo
                # aca, se le dice a quien opera que la extraccion (que suele correr primero)
                # deja la sesion verificada.
                raise DianError(
                    "El portal pidió verificación de identidad. Corre primero una "
                    "extracción (que sí sabe relevar el reto) y vuelve a intentar."
                )
            resultado = await session.escribir_borrador(titular, casillas)
            # EL PDF, EN LA MISMA SESION. Es la unica ventana en que se puede bajar sin pedir
            # la clave otra vez, y es lo que convierte "quedo escrito" en algo que un contador
            # puede mirar, archivar y mostrarle al cliente. Que falle no invalida la escritura:
            # el borrador YA quedo en el portal, y decir lo contrario seria mentir al reves.
            documento_id = await self._guardar_el_pdf(case_id, titular, session)
        finally:
            await session.close()

        resultado = resultado.model_copy(update={"documento_id": documento_id})

        # Se guarda DESPUES de que funciono: una clave que no sirvio no se archiva, porque
        # entonces el proximo paso la usaria sola y fallaria sin que nadie entienda por que.
        if password is not None:
            await self._clave.guardar(detail.client.id, password)

        veredicto = (
            "verificado renglón por renglón"
            if resultado.verificado
            else "CON DIFERENCIAS al releer"
        )
        await self._cases.add_event(
            case_id=case_id,
            kind="PORTAL_WRITE",
            message=(
                f"Se escribió el borrador {resultado.form_id} en el portal de la DIAN: "
                f"{resultado.escritas} casillas, {veredicto}."
            ),
        )
        log.info(
            "portal.write_done",
            case_id=str(case_id),
            form_id=resultado.form_id,
            verificado=resultado.verificado,
            con_pdf=documento_id is not None,
        )
        return resultado

    async def _guardar_el_pdf(
        self, case_id: UUID, titular: TaxpayerRef, session: object
    ) -> UUID | None:
        """Baja el borrador recien escrito y lo deja en el expediente.

        Se guarda con un tipo PROPIO y no como `SUGGESTED_RETURN`, que es el borrador que la
        DIAN precarga sola: son dos documentos distintos —uno es lo que la DIAN suponia y otro
        es lo que Clara escribio— y pisarlos entre si borraria justamente la comparacion que
        hace valioso al segundo.
        """
        try:
            raw = await session.download(  # type: ignore[attr-defined]
                DocumentType.SUGGESTED_RETURN, titular
            )
            stored = await self._store.put(taxpayer=titular, document=raw, scope_id=case_id)
            documento = await self._cases.add_document(
                case_id=case_id,
                doc_type=_DOC_BORRADOR,
                source=CaseDocumentSource.DIAN_PORTAL,
                storage_uri=stored.storage_uri,
                filename=f"borrador-210-{titular.tax_year}.pdf",
                content_sha256=stored.sha256,
            )
        except Exception as exc:
            # Un fallo aca NO puede tumbar la escritura: lo importante ya paso en el portal.
            log.warning("portal.pdf_no_guardado", case_id=str(case_id), error=str(exc)[:160])
            return None
        return documento.id
