"""Taxonomia de errores del dominio.

Cada error expone un `code` estable: ese codigo es contrato publico de la API y es
lo que el agente que nos consume debe usar para ramificar su conversacion. El texto
del mensaje puede cambiar; el codigo no.

`retryable` indica si reintentar tiene sentido tecnico. El caso critico es
DIAN_INVALID_CREDENTIALS: NO es reintentable de forma automatica porque el portal
bloquea la cuenta al tercer intento fallido.
"""

from __future__ import annotations

from typing import Any


class DeclarasError(Exception):
    """Raiz de todos los errores propios."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    retryable: bool = False
    default_message: str = "Ocurrio un error inesperado."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


# ─────────────────────────── Errores de entrada ───────────────────────────


class ValidationError(DeclarasError):
    code = "VALIDATION_ERROR"
    http_status = 422
    default_message = "Los datos enviados no son validos."


class JobNotFoundError(DeclarasError):
    code = "JOB_NOT_FOUND"
    http_status = 404
    default_message = "El trabajo solicitado no existe."


class JobStateConflictError(DeclarasError):
    code = "JOB_STATE_CONFLICT"
    http_status = 409
    default_message = "El trabajo no esta en un estado que permita esta operacion."


class DocumentNotFoundError(DeclarasError):
    code = "DOCUMENT_NOT_FOUND"
    http_status = 404
    default_message = "El documento solicitado no existe."


# ─────────────────────────── Errores del portal DIAN ───────────────────────────


class DianError(DeclarasError):
    """Base de las fallas que vienen del portal de la DIAN."""

    code = "DIAN_ERROR"
    http_status = 502
    default_message = "Falla al interactuar con el portal de la DIAN."


class DianInvalidCredentialsError(DianError):
    """La DIAN rechazo la clave.

    NO reintentar automaticamente: al tercer intento fallido el portal bloquea la
    cuenta y el usuario queda peor que como empezo.
    """

    code = "DIAN_INVALID_CREDENTIALS"
    http_status = 401
    retryable = False
    default_message = "La DIAN rechazo el usuario o la clave."


class DianLoginAttemptsExhaustedError(DianError):
    """Nuestro propio freno, antes de que la DIAN bloquee la cuenta."""

    code = "DIAN_LOGIN_ATTEMPTS_EXHAUSTED"
    http_status = 429
    retryable = False
    default_message = (
        "Se alcanzo el limite de intentos que permitimos para proteger la cuenta. "
        "Verifica la clave antes de volver a intentar."
    )


class DianAccountLockedError(DianError):
    code = "DIAN_ACCOUNT_LOCKED"
    http_status = 423
    retryable = False
    default_message = "La cuenta esta bloqueada en el portal de la DIAN."


class DianIdentityChallengeError(DianError):
    """El portal pidio algo que solo el contribuyente puede aportar.

    Es el disparador del patron relevo: el job queda AWAITING_CHALLENGE y el agente
    le pregunta al usuario por WhatsApp.
    """

    code = "DIAN_IDENTITY_CHALLENGE"
    http_status = 409
    retryable = False
    default_message = "El portal solicito una verificacion de identidad."


class DianPortalUnavailableError(DianError):
    code = "DIAN_PORTAL_UNAVAILABLE"
    http_status = 503
    retryable = True
    default_message = "El portal de la DIAN no esta disponible."


class DianTimeoutError(DianError):
    code = "DIAN_PORTAL_TIMEOUT"
    http_status = 504
    retryable = True
    default_message = "El portal de la DIAN no respondio en el tiempo esperado."


class DianRateLimitedError(DianError):
    code = "DIAN_RATE_LIMITED"
    http_status = 429
    retryable = True
    default_message = "El portal esta limitando las consultas."


class DianSessionExpiredError(DianError):
    code = "DIAN_SESSION_EXPIRED"
    http_status = 440
    retryable = True
    default_message = "La sesion en el portal expiro."


class DianLayoutChangedError(DianError):
    """No encontramos un elemento esperado: el portal cambio.

    No es reintentable y debe alertar al equipo: significa que hay que recalibrar
    selectores.
    """

    code = "DIAN_LAYOUT_CHANGED"
    http_status = 502
    retryable = False
    default_message = "El portal de la DIAN cambio y el conector necesita ajuste."


class DianDocumentUnavailableError(DianError):
    """El documento no existe todavia (caso tipico: exogena sin publicar)."""

    code = "DIAN_DOCUMENT_UNAVAILABLE"
    http_status = 404
    retryable = False
    default_message = "El documento no esta disponible en el portal."


# ─────────────────────────── Infraestructura ───────────────────────────


class StorageError(DeclarasError):
    code = "STORAGE_FAILURE"
    http_status = 500
    retryable = True
    default_message = "No se pudo almacenar o leer el documento."


# ─────────────────────────── Errores del expediente ───────────────────────────


class CaseNotFoundError(DeclarasError):
    code = "CASE_NOT_FOUND"
    http_status = 404
    default_message = "El expediente solicitado no existe."


class CaseAlreadyExistsError(DeclarasError):
    code = "CASE_ALREADY_EXISTS"
    http_status = 409
    default_message = "Ya existe un expediente de este cliente para ese anio gravable."


class FlagNotFoundError(DeclarasError):
    code = "FLAG_NOT_FOUND"
    http_status = 404
    default_message = "El flag solicitado no existe."
