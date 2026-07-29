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
    default_message = "Los datos enviados no son válidos."


class JobNotFoundError(DeclarasError):
    code = "JOB_NOT_FOUND"
    http_status = 404
    default_message = "El trabajo solicitado no existe."


class JobStateConflictError(DeclarasError):
    code = "JOB_STATE_CONFLICT"
    http_status = 409
    default_message = "El trabajo no está en un estado que permita esta operación."


class DocumentNotFoundError(DeclarasError):
    code = "DOCUMENT_NOT_FOUND"
    http_status = 404
    default_message = "El documento solicitado no existe."


class UnsupportedDocumentTypeError(DeclarasError):
    """No hay lector para esa clase de documento.

    Se distingue de DocumentUnreadableError a proposito: que aun no exista un parser es
    una limitacion conocida del sistema y no un problema del documento, asi que no debe
    generar una alerta al contador. Que un documento sea ilegible SI debe generarla.
    """

    code = "UNSUPPORTED_DOCUMENT_TYPE"
    http_status = 422
    default_message = "Todavía no hay lector para esa clase de documento."


class DocumentUnreadableError(DeclarasError):
    """El documento existe y deberia poder leerse, pero esta corrupto o no es del formato
    que dice ser. Es un problema real del archivo y el contador debe enterarse."""

    code = "DOCUMENT_UNREADABLE"
    http_status = 422
    default_message = (
        "El documento no se pudo leer. Puede estar dañado o no ser del formato esperado."
    )


class DocumentReaderUnavailableError(DeclarasError):
    """El lector no pudo trabajar: el documento no tiene nada malo.

    Es la tercera respuesta posible al leer, y se distingue de las otras dos porque lleva a una
    accion distinta. Que no haya lector (`UnsupportedDocumentTypeError`) no se arregla
    reintentando ni volviendo a pedir el archivo; que el documento este dañado
    (`DocumentUnreadableError`) se arregla volviendolo a pedir; que el lector con modelo no este
    disponible —sin credencial, sin cuota, con el proveedor caido— se arregla reintentando el
    mismo archivo, y por eso es el unico de los tres que es `retryable`.

    Sin esta clase, esas fallas subian hasta el manejador generico: 500 con `retryable: false`,
    o sea la respuesta contraria a la unica que sirve.
    """

    code = "DOCUMENT_READER_UNAVAILABLE"
    http_status = 503
    retryable = True
    default_message = (
        "El lector de documentos no está disponible en este momento. "
        "El archivo quedó guardado; hay que volver a intentar la lectura."
    )


# ─────────────────────────── Errores del portal DIAN ───────────────────────────


class DianError(DeclarasError):
    """Base de las fallas que vienen del portal de la DIAN."""

    code = "DIAN_ERROR"
    http_status = 502
    default_message = "Algo falló al hablar con el portal de la DIAN."


class DianInvalidCredentialsError(DianError):
    """La DIAN rechazo la clave.

    NO reintentar automaticamente: al tercer intento fallido el portal bloquea la
    cuenta y el usuario queda peor que como empezo.
    """

    code = "DIAN_INVALID_CREDENTIALS"
    http_status = 401
    retryable = False
    default_message = "La DIAN rechazó el usuario o la clave."


class DianLoginAttemptsExhaustedError(DianError):
    """Nuestro propio freno, antes de que la DIAN bloquee la cuenta."""

    code = "DIAN_LOGIN_ATTEMPTS_EXHAUSTED"
    http_status = 429
    retryable = False
    default_message = (
        "Se alcanzó el límite de intentos que permitimos para proteger la cuenta. "
        "Verifica la clave antes de volver a intentar."
    )


class DianAccountLockedError(DianError):
    code = "DIAN_ACCOUNT_LOCKED"
    http_status = 423
    retryable = False
    default_message = "La cuenta está bloqueada en el portal de la DIAN."


class DianIdentityChallengeError(DianError):
    """El portal pidio algo que solo el contribuyente puede aportar.

    Es el disparador del patron relevo: el job queda AWAITING_CHALLENGE y el agente
    le pregunta al usuario por WhatsApp.
    """

    code = "DIAN_IDENTITY_CHALLENGE"
    http_status = 409
    retryable = False
    default_message = "El portal pidió verificar la identidad del titular."


class DianPortalUnavailableError(DianError):
    code = "DIAN_PORTAL_UNAVAILABLE"
    http_status = 503
    retryable = True
    default_message = "El portal de la DIAN no está disponible."


class DianTimeoutError(DianError):
    code = "DIAN_PORTAL_TIMEOUT"
    http_status = 504
    retryable = True
    default_message = "El portal de la DIAN no respondió en el tiempo esperado."


class DianRateLimitedError(DianError):
    code = "DIAN_RATE_LIMITED"
    http_status = 429
    retryable = True
    default_message = "El portal está limitando las consultas."


class DianSessionExpiredError(DianError):
    code = "DIAN_SESSION_EXPIRED"
    http_status = 440
    retryable = True
    default_message = "La sesión en el portal se venció."


class DianLayoutChangedError(DianError):
    """No encontramos un elemento esperado: el portal cambio.

    No es reintentable y debe alertar al equipo: significa que hay que recalibrar
    selectores.
    """

    code = "DIAN_LAYOUT_CHANGED"
    http_status = 502
    retryable = False
    default_message = "El portal de la DIAN cambió y hay que ajustar el conector."


class DianDocumentUnavailableError(DianError):
    """El documento no existe todavia (caso tipico: exogena sin publicar)."""

    code = "DIAN_DOCUMENT_UNAVAILABLE"
    http_status = 404
    retryable = False
    default_message = "El documento no está disponible en el portal."


# ─────────────────────────── Infraestructura ───────────────────────────


class StorageError(DeclarasError):
    code = "STORAGE_FAILURE"
    http_status = 500
    retryable = True
    default_message = "No se pudo guardar o leer el documento."


class InvalidStorageReferenceError(StorageError):
    """La referencia apunta fuera del almacenamiento.

    Es distinto de una falla de almacenamiento y hay que tratarlo distinto: no es que el disco
    haya fallado, es que la peticion es invalida. Clasificarlo como falla reintentable, que era
    lo que pasaba, le decia a quien la mandara que valia la pena repetirla, y eso es exactamente
    lo que no se quiere responderle a un intento de salir del directorio.
    """

    code = "INVALID_STORAGE_REFERENCE"
    http_status = 400
    retryable = False
    default_message = "La referencia del documento no es válida."


# ─────────────────────────── Errores del expediente ───────────────────────────


class CaseNotFoundError(DeclarasError):
    code = "CASE_NOT_FOUND"
    http_status = 404
    default_message = "La declaración solicitada no existe."


class CaseAlreadyExistsError(DeclarasError):
    code = "CASE_ALREADY_EXISTS"
    http_status = 409
    default_message = "Ya existe una declaración de esa persona para ese año gravable."


class FlagNotFoundError(DeclarasError):
    code = "FLAG_NOT_FOUND"
    http_status = 404
    default_message = "El pendiente solicitado no existe."


# ─────────────────────────── Errores de la conciliacion ───────────────────────────


class SinReporteDeTercerosError(DeclarasError):
    """No hay reporte de terceros leido: no hay de donde abrir las partidas.

    Es 409 y no 404: la declaracion existe, lo que falta es el insumo. La accion es
    consultar la DIAN o subir el archivo, no buscar otra declaracion.
    """

    code = "SIN_REPORTE_DE_TERCEROS"
    http_status = 409
    default_message = (
        "Todavía no hay un reporte de terceros leído para esta declaración. "
        "Hay que consultar la DIAN o subir el archivo antes de conciliar."
    )


class PartidaNoEncontradaError(DeclarasError):
    code = "PARTIDA_NO_ENCONTRADA"
    http_status = 404
    default_message = "El renglón que se quiere resolver no existe en esta declaración."


class DecisionNoAplicaError(DeclarasError):
    """La decision no es posible para el estado (o el concepto) de ese renglon.

    Es 409 y no 422: el cuerpo esta bien formado, lo que no encaja es con el estado en que
    esta el renglon ahora mismo.
    """

    code = "DECISION_NO_APLICA"
    http_status = 409
    default_message = "Esa decisión no se puede tomar sobre este renglón."


class PeticionNoEncontradaError(DeclarasError):
    code = "PETICION_NO_ENCONTRADA"
    http_status = 404
    default_message = "El documento que se quiere cerrar no está en la lista de pendientes."


class LiquidacionNoDisponibleError(DeclarasError):
    """Todavia no se puede liquidar: quedan renglones sin decidir, o falta conciliar.

    Nunca se liquida por encima de un renglon sin resolver: esconderia plata, o la
    inventaria. El mensaje dice QUE falta.
    """

    code = "LIQUIDACION_NO_DISPONIBLE"
    http_status = 409
    default_message = "Todavía no se puede calcular la declaración: quedan renglones sin decidir."


class LiquidacionBloqueadaError(DeclarasError):
    """Hay una alerta bloqueante viva: la declaracion se puede ver, pero no darse por buena.

    El caso real: un ingreso que el motor no liquida y que el contador tiene que sumar a
    mano. Cerrar el borrador con esa alerta viva seria dar por completo un formulario al
    que le falta un ingreso.
    """

    code = "LIQUIDACION_BLOQUEADA"
    http_status = 409
    default_message = "Esta declaración tiene alertas que hay que atender antes de darla por lista."


class AnioSinParametrosError(DeclarasError):
    """El ano gravable del expediente no tiene tabla de parametros calibrada.

    Es 409 y no 500: no fallo el servidor, es que ese ano todavia no se liquida. Se abren
    expedientes desde 2015 y las tablas se calibran ano por ano.
    """

    code = "ANIO_SIN_PARAMETROS"
    http_status = 409
    default_message = (
        "Todavía no está calibrada la tabla de ese año gravable, así que no se puede "
        "calcular la declaración."
    )


class ConflictoDeConcurrenciaError(DeclarasError):
    """Alguien mas cambio este expediente entre que se leyo y se iba a guardar.

    Se responde en vez de sobrescribir: guardar encima perderia la decision de la otra
    persona en silencio, y esta API afirmaria haber guardado algo que no guardo.
    """

    code = "CONFLICTO_DE_CONCURRENCIA"
    http_status = 409
    retryable = True
    default_message = (
        "Alguien más cambió esta declaración mientras se preparaba el cambio. "
        "Hay que volver a cargarla y repetir la decisión."
    )


class TaxpayerMismatchError(DeclarasError):
    """Los datos que se intentan vincular pertenecen a otro contribuyente.

    Es la proteccion mas importante del expediente: mezclar la informacion tributaria de
    dos personas seria un dano grave y muy dificil de detectar despues.
    """

    code = "TAXPAYER_MISMATCH"
    http_status = 409
    default_message = "La información pertenece a otra persona o a otro año gravable."
