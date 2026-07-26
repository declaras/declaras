"""Aviso al agente que nos consume cuando un job termina.

Existe para que el agente no tenga que hacer polling: si registra callback_url, le
avisamos. El envio es best effort y nunca tumba el job, porque el estado autoritativo
siempre esta en GET /extractions/{id}.
"""

from __future__ import annotations

from typing import Any

import httpx

from declaras.observability import get_logger

log = get_logger(__name__)

_TIMEOUT_S = 10.0


class WebhookNotifier:
    async def notify(self, callback_url: str, payload: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                response = await client.post(callback_url, json=payload)
            ok = response.is_success
            log.info("notifier.sent", status_code=response.status_code, ok=ok)
            return ok
        except httpx.HTTPError as exc:
            log.warning("notifier.failed", error=str(exc)[:200])
            return False
