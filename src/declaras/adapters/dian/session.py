"""Sesion Playwright contra el Muisca. Implementa el puerto DianSession."""

from __future__ import annotations

from uuid import uuid4

from playwright.async_api import Page

from declaras.adapters.dian.browser import ManagedContext
from declaras.adapters.dian.flows.documents import DOWNLOADERS
from declaras.adapters.dian.flows.login import resolve_challenge
from declaras.domain.errors import (
    DianIdentityChallengeError,
    DianLayoutChangedError,
    ValidationError,
)
from declaras.domain.models import (
    BorradorEscrito,
    ChallengeAnswer,
    DocumentType,
    IdentityChallenge,
    RawDocument,
    TaxpayerRef,
)
from declaras.observability import get_logger

log = get_logger(__name__)


class PlaywrightDianSession:
    """Envuelve una pagina autenticada y expone las descargas del portal."""

    def __init__(
        self,
        *,
        managed_context: ManagedContext,
        page: Page,
        base_url: str,
        challenge: IdentityChallenge | None = None,
    ) -> None:
        self.session_id = str(uuid4())
        self._ctx = managed_context
        self._page = page
        self._base_url = base_url
        self._challenge = challenge
        self._closed = False

    @property
    def pending_challenge(self) -> IdentityChallenge | None:
        return self._challenge

    async def download(self, doc_type: DocumentType, taxpayer: TaxpayerRef) -> RawDocument:
        self._assert_ready()
        downloader = DOWNLOADERS.get(doc_type)
        if downloader is None:
            raise ValidationError(
                f"El documento {doc_type.value} no se puede descargar.", doc_type=doc_type.value
            )

        log.info("dian.download.start", doc_type=doc_type.value, session_id=self.session_id)
        document = await downloader(self._page, self._base_url, taxpayer)
        log.info("dian.download.done", doc_type=doc_type.value, size_bytes=len(document.content))
        return document

    async def escribir_borrador(
        self, taxpayer: TaxpayerRef, casillas: dict[int, int]
    ) -> BorradorEscrito:
        """El conector de navegador NO escribe, y la negativa es explicita a proposito.

        La escritura vive en el conector HTTP, que habla con la API del formato
        (`renta210v18`) y verifica con relectura. Reproducir eso a punta de DOM seria una
        segunda implementacion de la operacion mas delicada del producto: la que modifica
        la cuenta del contribuyente.
        """
        raise DianLayoutChangedError(
            "Escribir el 210 requiere el conector HTTP (DECLARAS_DIAN_ADAPTER=http). "
            "El conector de navegador solo descarga.",
            doc_type="FORM_210_WRITE",
        )

    async def capture_evidence(self, label: str) -> RawDocument:
        """Captura de pantalla para el expediente de auditoria."""
        screenshot = await self._page.screenshot(full_page=True)
        return RawDocument(
            doc_type=DocumentType.EVIDENCE,
            filename=f"{label}.png",
            content=screenshot,
            content_type="image/png",
            source_url=self._page.url,
            metadata={"label": label, "session_id": self.session_id},
        )

    async def answer_challenge(self, answer: ChallengeAnswer) -> None:
        if self._challenge is None:
            raise ValidationError("La sesión no tiene ninguna verificación pendiente.")
        outcome = await resolve_challenge(self._page, answer.answers)
        if outcome.authenticated:
            self._challenge = None
            log.info("dian.session.challenge_resolved", session_id=self.session_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._ctx.close()
        log.info("dian.session.closed", session_id=self.session_id)

    def _assert_ready(self) -> None:
        if self._challenge is not None:
            raise DianIdentityChallengeError(
                "La sesión espera la verificación de identidad del titular.",
                challenge=self._challenge.model_dump(mode="json"),
            )
        if self._closed:
            raise ValidationError("La sesión ya fue cerrada.")
