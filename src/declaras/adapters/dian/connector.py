"""Conector real contra el Muisca. Implementa el puerto DianConnector."""

from __future__ import annotations

from declaras.adapters.dian.browser import BrowserPool
from declaras.adapters.dian.flows.login import perform_login
from declaras.adapters.dian.session import PlaywrightDianSession
from declaras.domain.models import DianCredentials, TaxpayerRef
from declaras.observability import get_logger

log = get_logger(__name__)


class PlaywrightDianConnector:
    """Abre sesiones autenticadas en el portal de la DIAN."""

    def __init__(self, *, pool: BrowserPool, base_url: str, challenge_ttl_s: int) -> None:
        self._pool = pool
        self._base_url = base_url
        self._challenge_ttl_s = challenge_ttl_s

    async def open_session(
        self, credentials: DianCredentials, taxpayer: TaxpayerRef
    ) -> PlaywrightDianSession:
        managed = await self._pool.acquire_context()
        try:
            page = await managed.new_page()
            outcome = await perform_login(
                page,
                base_url=self._base_url,
                credentials=credentials,
                challenge_ttl_s=self._challenge_ttl_s,
            )
            return PlaywrightDianSession(
                managed_context=managed,
                page=page,
                base_url=self._base_url,
                challenge=outcome.challenge,
            )
        except Exception:
            # Cualquier falla libera el contexto y el cupo de concurrencia.
            await managed.close()
            raise

    async def shutdown(self) -> None:
        await self._pool.stop()
