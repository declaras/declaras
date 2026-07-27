"""Seleccion del conector DIAN segun configuracion."""

from __future__ import annotations

from declaras.config import Settings
from declaras.config.settings import DianAdapterKind
from declaras.domain.ports import DianConnector


def build_dian_connector(settings: Settings) -> DianConnector:
    if settings.dian_adapter is DianAdapterKind.FAKE:
        if settings.is_production:
            raise ValueError("El conector de prueba no puede usarse en producción.")
        from declaras.adapters.dian.fake import FakeDianConnector

        return FakeDianConnector(challenge_ttl_s=settings.dian_challenge_ttl_s)

    if settings.dian_adapter is DianAdapterKind.HTTP:
        from declaras.adapters.dian.rest.connector import HttpDianConnector

        return HttpDianConnector(base_url=settings.dian_base_url)

    from declaras.adapters.dian.browser import BrowserPool
    from declaras.adapters.dian.connector import PlaywrightDianConnector

    pool = BrowserPool(
        headless=settings.dian_headless,
        max_concurrent=settings.dian_max_concurrent_sessions,
        nav_timeout_ms=settings.dian_nav_timeout_ms,
    )
    return PlaywrightDianConnector(
        pool=pool,
        base_url=settings.dian_base_url,
        challenge_ttl_s=settings.dian_challenge_ttl_s,
    )
