"""Logging estructurado con redaccion de secretos.

Regla dura del proyecto: una clave de la DIAN nunca puede terminar en un log. Aca se
centraliza esa garantia con un procesador que enmascara llaves sensibles y cualquier
valor que parezca una credencial.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "clave",
        "contrasena",
        "contraseña",
        "credentials",
        "dian_password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "answers",
    }
)

_SECRET_VALUE_RE = re.compile(r"SecretStr\(['\"].*?['\"]\)")
_MASK = "***"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_MASK if k.lower() in SENSITIVE_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact(v) for v in value)
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(f"SecretStr('{_MASK}')", value)
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Enmascara secretos en todo evento antes de emitirlo."""
    return {
        key: (_MASK if key.lower() in SENSITIVE_KEYS else _redact(val))
        for key, val in event_dict.items()
    }


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
