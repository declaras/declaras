"""Lectores de certificados que aporta el cliente.

A diferencia de los documentos del portal (columnas fijas, parser determinístico), cada
emisor arma su certificado como quiere: se leen con un modelo y por eso cada campo viaja
con la confianza que el modelo declaró.

ESTE MODULO ES LA FRONTERA, Y POR ESO TRADUCE

`extraer_220` es el núcleo de extracción: levanta `ValueError` con el detalle técnico
(`stop_reason` del SDK, las dos cifras que no reconcilian) porque su contrato es con quien
programa. Del otro lado de esta función hay dos consumidores que le hablan a una persona:
el expediente, que convierte una falla de lectura en una alerta que lee el contador, y la
API. Un `ValueError` crudo no lo atrapa ninguno de los dos (el expediente espera
`DocumentUnreadableError`, y la API lo degradaría a un 500 genérico), así que acá se
traduce una vez: falla de dominio con mensaje escrito para quien subió el archivo, y el
detalle técnico al log, que es donde sirve.
"""

from __future__ import annotations

from typing import Any

from declaras.documents.models import DocumentReading, ExtractedField
from declaras.domain.errors import DocumentReaderUnavailableError, DocumentUnreadableError
from declaras.extraccion.f220 import extraer_220_con_metadatos, id_documento
from declaras.observability import get_logger

log = get_logger(__name__)

PARSER_220 = "cert_220.llm.v1"


def leer_220(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de ingresos y retenciones (formulario 220)."""
    try:
        laboral, extraccion = extraer_220_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        )
    except ValueError as exc:
        # `ValueError` cubre las dos formas de falla del extractor: sus propios guards y la
        # validación del esquema (la de pydantic hereda de `ValueError`). El texto queda en
        # el log, recortado, y no viaja en `details`: `to_payload` los devuelve por la API.
        log.warning("documents.cert_220.unreadable", detalle=str(exc)[:200])
        raise DocumentUnreadableError(
            "No se pudo leer el certificado de ingresos y retenciones. Revisa que sea el "
            "certificado del año que se está declarando y que el archivo esté completo.",
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
        content_sha256=id_documento(content),
        fields=[ExtractedField(name=k, value=v, confidence=confianza) for k, v in campos.items()],
    )
