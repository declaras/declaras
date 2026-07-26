"""Sesion HTTP contra el Muisca. Implementa el puerto DianSession sin navegador.

La sesion no sabe como se baja cada documento: eso vive en `flows`. Aqui solo esta el
ciclo de vida y la traduccion al modelo del dominio.
"""

from __future__ import annotations

from uuid import uuid4

import httpx

from declaras.adapters.dian.rest.client import PortalClient
from declaras.adapters.dian.rest.flows import DOWNLOADERS
from declaras.domain.errors import DianLayoutChangedError, ValidationError
from declaras.domain.models import (
    ChallengeAnswer,
    DocumentType,
    IdentityChallenge,
    RawDocument,
    TaxpayerRef,
)
from declaras.observability import get_logger

log = get_logger(__name__)


class HttpDianSession:
    """Sesion autenticada sobre httpx: sin Chromium, sin DOM."""

    def __init__(self, *, client: httpx.AsyncClient, base_url: str) -> None:
        self.session_id = str(uuid4())
        self._portal = PortalClient(client, base_url)
        self._closed = False

    @property
    def pending_challenge(self) -> IdentityChallenge | None:
        """El flujo HTTP no observa retos: si el portal los exige, el login falla."""
        return None

    async def download(self, doc_type: DocumentType, taxpayer: TaxpayerRef) -> RawDocument:
        self._assert_open()
        downloader = DOWNLOADERS.get(doc_type)
        if downloader is None:
            raise DianLayoutChangedError(
                f"{doc_type.value} aun no esta calibrado para el conector HTTP",
                doc_type=doc_type.value,
            )
        log.info("dian.http.download_start", doc_type=doc_type.value, session_id=self.session_id)
        return await downloader(self._portal, taxpayer)

    async def capture_evidence(self, label: str) -> RawDocument:
        """Sin navegador no hay captura de pantalla: se archiva el HTML del portal.

        Cumple el mismo proposito de auditoria y ademas sirve para depurar cuando el
        portal cambia.
        """
        html = await self._portal.fetch_dashboard()
        return RawDocument(
            doc_type=DocumentType.EVIDENCE,
            filename=f"{label}.html",
            content=html.encode("utf-8", errors="replace"),
            content_type="text/html",
            source_url=self._portal.dashboard_url,
            metadata={"label": label, "session_id": self.session_id, "via": "http"},
        )

    async def answer_challenge(self, answer: ChallengeAnswer) -> None:
        raise ValidationError("el conector HTTP no maneja verificacion de identidad")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._portal.aclose()
        log.info("dian.http.session_closed", session_id=self.session_id)

    def _assert_open(self) -> None:
        if self._closed:
            raise ValidationError("la sesion ya fue cerrada")
