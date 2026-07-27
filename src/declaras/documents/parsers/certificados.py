"""Lectores de certificados que aporta el cliente.

A diferencia de los documentos del portal (columnas fijas, parser determinístico), cada
emisor arma su certificado como quiere: se leen con un modelo y por eso cada campo viaja
con la confianza que el modelo declaró.

ESTE MODULO ES LA FRONTERA, Y POR ESO TRADUCE

El núcleo de extracción falla con el detalle técnico a la vista (`stop_reason` del SDK, las dos
cifras que no reconcilian) porque su contrato es con quien programa. Del otro lado de esta
función hay dos consumidores que le hablan a una persona: el expediente, que convierte una falla
de lectura en una alerta que lee el contador, y la API. Ninguno de los dos atrapa un `ValueError`
crudo —el expediente espera las fallas del dominio, y la API degradaría cualquier otra cosa a un
500 genérico—, así que acá se traduce, una vez, distinguiendo de quién es el problema:

  del documento   `DocumentUnreadableError` (422), con la pista que corresponde a la causa.
  del lector      `DocumentReaderUnavailableError` (503, reintentable): sin credencial, sin
                  cuota o con el proveedor caído. El archivo no tiene nada malo.

El texto técnico va al log, que es donde sirve, y nunca al mensaje ni a `details`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from declaras.documents.models import DocumentReading, ExtractedField
from declaras.domain.errors import DocumentReaderUnavailableError, DocumentUnreadableError
from declaras.extraccion.f220 import (
    Extraccion220InvalidaError,
    Motivo220,
    extraer_220_con_metadatos,
)
from declaras.observability import get_logger

log = get_logger(__name__)

PARSER_220 = "cert_220.llm.v1"

# Lo que se le dice a la persona por cada causa de rechazo. Es un COMPLEMENTO del prefijo que
# arma el expediente ("No se pudo leer el certificado de ingresos y retenciones: …"), igual que
# los mensajes de los cuatro parsers hermanos, y por eso ninguno repite el nombre del documento.
#
# Una pista por causa y no una sola genérica: las causas piden acciones distintas, y "revisa que
# el archivo esté completo" frente a un certificado que trae pensiones manda a pedir de nuevo un
# archivo que estaba bien, con un flag que va a quedar igual para siempre.
PISTAS: dict[Motivo220, str] = {
    Motivo220.NO_ES_PDF: "El archivo no es un PDF que se pueda leer.",
    # Sin salida estructurada: el modelo se negó o se quedó sin presupuesto. Lo que puede hacer
    # quien subió el archivo es mandar una copia mejor; el detalle técnico va al log.
    Motivo220.SIN_SALIDA: (
        "No se pudieron leer las cifras del certificado. Si está escaneado o se ve borroso, "
        "una copia más nítida ayuda."
    ),
    Motivo220.VARIOS_CERTIFICADOS: (
        "El archivo trae más de un certificado, y hay que subirlos de a uno."
    ),
    Motivo220.OTRO_ANIO: (
        "El certificado es de otro año gravable: se necesita el del año que se está declarando."
    ),
    Motivo220.NO_RECONCILIA: (
        "Las cifras leídas no suman el total impreso en el certificado, así que hay que "
        "revisarlo a mano."
    ),
    Motivo220.TIENE_PENSIONES: (
        "El certificado incluye pagos de pensión, que se registran aparte de los salarios."
    ),
}

# Para una falla que no viene de un guard con motivo: la validación del esquema, o un guard nuevo
# al que nadie le escribió su pista.
PISTA_GENERICA = "El certificado no quedó legible."


def leer_220(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de ingresos y retenciones (formulario 220)."""
    try:
        laboral, extraccion = extraer_220_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        )
    except ValueError as exc:
        # `ValueError` cubre las dos formas de falla del extractor: sus propios guards (que
        # además dicen cuál falló) y la validación del esquema, cuyo error de pydantic hereda de
        # `ValueError` y no trae motivo. El texto técnico queda en el log, recortado, y no viaja
        # en `details`: `to_payload` los devuelve por la API.
        motivo = exc.motivo if isinstance(exc, Extraccion220InvalidaError) else None
        log.warning("documents.cert_220.unreadable", motivo=motivo, detalle=str(exc)[:200])
        raise DocumentUnreadableError(
            PISTAS.get(motivo, PISTA_GENERICA) if motivo else PISTA_GENERICA,
            parser=PARSER_220,
        ) from exc
    except Exception as exc:
        # Todo lo demás es el lector, no el documento: sin credencial el SDK ni siquiera falla
        # con `ValueError` (revienta con `TypeError` al resolver la autenticación), y una cuota
        # agotada, un 429 o un 529 tampoco son culpa del archivo. Sin esta rama suben hasta el
        # manejador genérico —500, `retryable: false`— y el certificado queda en el expediente
        # sin lectura y sin alerta, que es el mismo silencio que esta frontera existe para
        # tapar, abierto justo para la falla más probable.
        log.warning(
            "documents.cert_220.reader_unavailable",
            error=type(exc).__name__,
            detalle=str(exc)[:200],
        )
        raise DocumentReaderUnavailableError(parser=PARSER_220) from exc

    confianza = laboral.fuente.confianza or 0.0
    campos: dict[str, Any] = {
        # El año del certificado, no el que se esperaba: cuando nadie ató un año esperado (una
        # lectura sin contexto de caso) el guard no corrió, y este es el único dato con el que
        # después se puede ver que el certificado no corresponde al caso.
        "anio_gravable": extraccion.anio_gravable,
        "empleador_nit": laboral.empleador_nit,
        "empleador_nombre": laboral.empleador_nombre,
        "salarios": laboral.salarios,
        "cesantias_e_intereses": laboral.cesantias_e_intereses,
        "prima": laboral.prima,
        "bonificaciones": laboral.bonificaciones,
        "aportes_salud": laboral.aportes_salud,
        "aportes_pension": laboral.aportes_pension,
        "retencion": laboral.retencion,
    }
    return DocumentReading(
        doc_type="CERT_INGRESOS_220",
        parser=PARSER_220,
        # El digest COMPLETO, como los cuatro lectores del portal y como el documento del
        # expediente: `content_sha256` no puede significar una cosa en una familia y otra en la
        # otra, o un cruce lectura↔documento por hash falla solo para el 220. El prefijo de 12
        # con el que `Fuente.ref` identifica el documento se saca de acá (`id_documento`).
        content_sha256=hashlib.sha256(content).hexdigest(),
        fields=[ExtractedField(name=k, value=v, confidence=confianza) for k, v in campos.items()],
    )
