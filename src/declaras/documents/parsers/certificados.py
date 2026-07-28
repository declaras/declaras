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
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from declaras.documents.models import DocumentReading, ExtractedField, ReadingWarning
from declaras.domain.errors import DocumentReaderUnavailableError, DocumentUnreadableError
from declaras.extraccion.cert_arriendo import MotivoArriendo, extraer_arriendo_con_metadatos
from declaras.extraccion.cert_bancario import MotivoBancario, extraer_bancario_con_metadatos
from declaras.extraccion.cert_beneficio import (
    DOC_TYPE_POR_TIPO,
    TIPO_POR_DOC_TYPE,
    MotivoBeneficio,
    TipoBeneficio,
    extraer_beneficio_con_metadatos,
)
from declaras.extraccion.cert_dividendos import (
    MotivoDividendos,
    extraer_dividendos_con_metadatos,
)
from declaras.extraccion.cert_pension import MotivoPension, extraer_pension_con_metadatos
from declaras.extraccion.f220 import Motivo220, extraer_220_con_metadatos
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

PARSER_PENSION = "cert_pension.llm.v1"
PARSER_BANCARIO = "cert_bancario.llm.v1"
PARSER_DIVIDENDOS = "cert_dividendos.llm.v1"
PARSER_ARRIENDO = "cert_arriendo.llm.v1"
PARSER_BENEFICIO = "cert_beneficio.llm.v1"

# Las tres primeras causas son las de la base y se repiten en los cinco certificados; las de
# abajo son las propias de cada uno. Cada pista dice qué puede hacer quien subió el archivo:
# una genérica frente a un certificado de pensión sin detalle mensual manda a mandar otra vez
# el mismo documento, y el pendiente se repite igual para siempre.
_PISTA_NO_ES_PDF = "El archivo no es un PDF que se pueda leer."
_PISTA_SIN_SALIDA = (
    "No se pudieron leer las cifras del certificado. Si está escaneado o se ve borroso, "
    "una copia más nítida ayuda."
)
_PISTA_OTRO_ANIO = (
    "El certificado es de otro año gravable: se necesita el del año que se está declarando."
)

PISTAS_PENSION: dict[MotivoPension, str] = {
    MotivoPension.NO_ES_PDF: _PISTA_NO_ES_PDF,
    MotivoPension.SIN_SALIDA: _PISTA_SIN_SALIDA,
    MotivoPension.OTRO_ANIO: _PISTA_OTRO_ANIO,
    MotivoPension.MESADAS_INCOMPLETAS: (
        "El certificado no trae el detalle de los doce meses, y la exención de la pensión se "
        "calcula mes a mes: hay que pedir el certificado con el desglose mensual."
    ),
    MotivoPension.NO_RECONCILIA: (
        "Las mesadas leídas no suman el total impreso en el certificado, así que hay que "
        "revisarlo a mano."
    ),
}

PISTAS_BANCARIO: dict[MotivoBancario, str] = {
    MotivoBancario.NO_ES_PDF: _PISTA_NO_ES_PDF,
    MotivoBancario.SIN_SALIDA: _PISTA_SIN_SALIDA,
    MotivoBancario.OTRO_ANIO: _PISTA_OTRO_ANIO,
    MotivoBancario.SIN_CUENTAS: (
        "El certificado no dice a qué cuentas o productos corresponden las cifras."
    ),
}

PISTAS_DIVIDENDOS: dict[MotivoDividendos, str] = {
    MotivoDividendos.NO_ES_PDF: _PISTA_NO_ES_PDF,
    MotivoDividendos.SIN_SALIDA: _PISTA_SIN_SALIDA,
    MotivoDividendos.OTRO_ANIO: _PISTA_OTRO_ANIO,
    MotivoDividendos.NO_DISCRIMINA: (
        "El certificado no separa los dividendos gravados de los no gravados, y cada parte "
        "paga distinto: hay que pedirle a la sociedad el certificado con esa separación."
    ),
    MotivoDividendos.NO_RECONCILIA: (
        "Las cifras leídas no suman el total impreso en el certificado, así que hay que "
        "revisarlo a mano."
    ),
}

PISTAS_ARRIENDO: dict[MotivoArriendo, str] = {
    MotivoArriendo.NO_ES_PDF: _PISTA_NO_ES_PDF,
    MotivoArriendo.SIN_SALIDA: _PISTA_SIN_SALIDA,
    MotivoArriendo.OTRO_ANIO: _PISTA_OTRO_ANIO,
    MotivoArriendo.CANON_VACIO: (
        "El certificado no reporta cánones recibidos en el año, así que no hay arriendo que "
        "declarar con este documento."
    ),
}


def _traducir[R](
    leer: Callable[[], R],
    *,
    etiqueta: str,
    parser: str,
    pistas: Mapping[Any, str],
) -> R:
    """Corre un extractor y traduce sus dos formas de falla. Es el cuerpo de la frontera.

    Vive aparte porque son cinco certificados con la misma traducción y una sola diferencia
    —su tabla de pistas—. Con una copia por lector, el día que se descubra un tercer tipo de
    falla se arregla en uno y se olvida en los otros cuatro; ya pasó en este proyecto con la
    rama del lector caído, que existió solo en el 220 durante una tarea entera.
    """
    try:
        return leer()
    except ValueError as exc:
        # `ValueError` cubre las dos formas de falla del extractor: sus propios guards (que
        # además dicen cuál falló) y la validación del esquema, cuyo error de pydantic hereda de
        # `ValueError` y no trae motivo. El texto técnico queda en el log, recortado, y no viaja
        # en `details`: `to_payload` los devuelve por la API.
        motivo = getattr(exc, "motivo", None)
        log.warning(f"documents.{etiqueta}.unreadable", motivo=motivo, detalle=str(exc)[:200])
        raise DocumentUnreadableError(
            pistas.get(motivo, PISTA_GENERICA) if motivo else PISTA_GENERICA,
            parser=parser,
        ) from exc
    except Exception as exc:
        # Todo lo demás es el lector, no el documento: sin credencial el SDK ni siquiera falla
        # con `ValueError` (revienta con `TypeError` al resolver la autenticación), y una cuota
        # agotada, un 429 o un 529 tampoco son culpa del archivo. Sin esta rama suben hasta el
        # manejador genérico —500, `retryable: false`— y el certificado queda en el expediente
        # sin lectura y sin alerta, que es el mismo silencio que esta frontera existe para
        # tapar, abierto justo para la falla más probable.
        log.warning(
            f"documents.{etiqueta}.reader_unavailable",
            error=type(exc).__name__,
            detalle=str(exc)[:200],
        )
        raise DocumentReaderUnavailableError(parser=parser) from exc


def _lectura(
    content: bytes,
    *,
    doc_type: str,
    parser: str,
    campos: dict[str, Any],
    confianza: float,
    warnings: list[ReadingWarning] | None = None,
) -> DocumentReading:
    """Arma la lectura con el digest completo y la confianza del modelo en cada campo."""
    return DocumentReading(
        doc_type=doc_type,
        parser=parser,
        # El digest COMPLETO, como los cuatro lectores del portal y como el documento del
        # expediente: `content_sha256` no puede significar una cosa en una familia y otra en la
        # otra, o un cruce lectura↔documento por hash falla solo para esta familia. El prefijo
        # de 12 con el que `Fuente.ref` identifica el documento se saca de acá (`id_documento`).
        content_sha256=hashlib.sha256(content).hexdigest(),
        fields=[ExtractedField(name=k, value=v, confidence=confianza) for k, v in campos.items()],
        warnings=warnings or [],
    )


def leer_220(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de ingresos y retenciones (formulario 220)."""
    laboral, extraccion = _traducir(
        lambda: extraer_220_con_metadatos(content, anio_esperado=anio_esperado, client=client),
        etiqueta="cert_220",
        parser=PARSER_220,
        pistas=PISTAS,
    )

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
    return _lectura(
        content,
        doc_type="CERT_INGRESOS_220",
        parser=PARSER_220,
        campos=campos,
        confianza=confianza,
    )


def leer_pension(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de pensión."""
    pension, ext = _traducir(
        lambda: extraer_pension_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        ),
        etiqueta="cert_pension",
        parser=PARSER_PENSION,
        pistas=PISTAS_PENSION,
    )
    return _lectura(
        content,
        doc_type="CERT_PENSION",
        parser=PARSER_PENSION,
        campos={
            "anio_gravable": ext.anio_gravable,
            "pagador_nit": pension.pagador_nit,
            "pagador_nombre": pension.pagador,
            # El total anual es lo que cruza contra la exógena, que reporta la pensión
            # agregada por pagador. Las doce mesadas viajan aparte porque son el detalle del
            # que depende la exención mensual, y ninguna fila de la exógena las tiene.
            "total_pagado": sum(pension.mesadas),
            "mesadas": list(pension.mesadas),
            "retencion": pension.retencion,
        },
        confianza=pension.fuente.confianza or 0.0,
    )


def leer_bancario(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado tributario bancario (rendimientos, retención y GMF)."""
    rendimiento, gmf, ext = _traducir(
        lambda: extraer_bancario_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        ),
        etiqueta="cert_bancario",
        parser=PARSER_BANCARIO,
        pistas=PISTAS_BANCARIO,
    )
    return _lectura(
        content,
        doc_type="CERT_BANCARIO",
        parser=PARSER_BANCARIO,
        campos={
            "anio_gravable": ext.anio_gravable,
            "entidad_nit": rendimiento.entidad_nit,
            "entidad_nombre": rendimiento.entidad,
            "rendimientos": rendimiento.valor,
            "retencion": rendimiento.retencion,
            # El GMF viaja en la lectura pero NO abre partida: no es ingreso, es un impuesto
            # pagado que va a los beneficios del caso. Queda acá para que no se pierda el dato
            # del certificado; llevarlo hasta `Beneficios.gmf_pagado` es del camino de
            # beneficios, no del cruce de ingresos.
            "gmf_pagado": gmf.valor if gmf else 0,
            "numero_de_cuentas": ext.numero_de_cuentas,
            "saldo_31_dic": ext.saldo_31_dic,
        },
        confianza=rendimiento.fuente.confianza or 0.0,
    )


def leer_dividendos(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de dividendos, que solo es legible si discrimina las dos bolsas."""
    dividendo, ext = _traducir(
        lambda: extraer_dividendos_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        ),
        etiqueta="cert_dividendos",
        parser=PARSER_DIVIDENDOS,
        pistas=PISTAS_DIVIDENDOS,
    )
    return _lectura(
        content,
        doc_type="CERT_DIVIDENDOS",
        parser=PARSER_DIVIDENDOS,
        campos={
            "anio_gravable": ext.anio_gravable,
            "anio_utilidades": ext.anio_utilidades,
            "sociedad_nit": dividendo.sociedad_nit,
            "sociedad_nombre": dividendo.sociedad_nombre,
            "gravados": dividendo.gravados,
            "no_gravados": dividendo.no_gravados,
            "retencion": dividendo.retencion,
        },
        confianza=dividendo.fuente.confianza or 0.0,
    )


def leer_arriendo(
    content: bytes, *, anio_esperado: int | None = None, client: Any = None
) -> DocumentReading:
    """Lee un certificado de arrendamiento, con su aviso de revisión si los costos exceden."""
    arriendo, aviso, ext = _traducir(
        lambda: extraer_arriendo_con_metadatos(
            content, anio_esperado=anio_esperado, client=client
        ),
        etiqueta="cert_arriendo",
        parser=PARSER_ARRIENDO,
        pistas=PISTAS_ARRIENDO,
    )
    return _lectura(
        content,
        doc_type="CERT_ARRIENDO",
        parser=PARSER_ARRIENDO,
        campos={
            "anio_gravable": ext.anio_gravable,
            "contraparte_nit": arriendo.contraparte_nit,
            "contraparte_nombre": arriendo.contraparte_nombre,
            "inmueble": arriendo.inmueble,
            "canon_total": arriendo.canon_total,
            "retencion": arriendo.retencion,
            "predial": arriendo.costos.predial,
            "administracion": arriendo.costos.administracion,
            "comision_inmobiliaria": arriendo.costos.comision_inmobiliaria,
            "reparaciones": arriendo.costos.reparaciones,
            "meses": ext.meses,
        },
        confianza=arriendo.fuente.confianza or 0.0,
        # El aviso no cambia ninguna cifra: viaja como warning para que el expediente lo
        # convierta en un pendiente del contador.
        warnings=[aviso] if aviso else None,
    )


PISTAS_BENEFICIO: dict[MotivoBeneficio, str] = {
    MotivoBeneficio.NO_ES_PDF: _PISTA_NO_ES_PDF,
    MotivoBeneficio.SIN_SALIDA: _PISTA_SIN_SALIDA,
    MotivoBeneficio.OTRO_ANIO: _PISTA_OTRO_ANIO,
    MotivoBeneficio.TIPO_QUE_NO_COINCIDE: (
        "El archivo no es el certificado que se pidió: parece ser de otro beneficio. "
        "Revisa que sea el que corresponde a esta solicitud."
    ),
    MotivoBeneficio.SIN_VALOR: (
        "El certificado no reporta ningún valor pagado en el año, así que no hay beneficio "
        "que aplicar con este documento."
    ),
    MotivoBeneficio.SIN_CERTIFICAR: (
        "El archivo no es un certificado formal de la entidad. Un extracto o una captura de "
        "pantalla no sirve de soporte si la DIAN pregunta: hay que pedirle el certificado a "
        "la entidad."
    ),
}


def _leer_beneficio(
    content: bytes,
    *,
    doc_type: str,
    anio_esperado: int | None = None,
    client: Any = None,
) -> DocumentReading:
    """Lee un certificado de beneficio, validando que sea del beneficio que se pidió.

    Los cinco tipos comparten lector porque comparten extractor. El `doc_type` con el que se
    despachó es el hint: si el documento resulta ser de otro beneficio, la extracción falla en
    vez de meter la cifra bajo el tope equivocado.
    """
    esperado = TIPO_POR_DOC_TYPE[doc_type]
    tipo, monto, ext = _traducir(
        lambda: extraer_beneficio_con_metadatos(
            content, tipo=esperado, anio_esperado=anio_esperado, client=client
        ),
        etiqueta="cert_beneficio",
        parser=PARSER_BENEFICIO,
        pistas=PISTAS_BENEFICIO,
    )
    return _lectura(
        content,
        doc_type=DOC_TYPE_POR_TIPO[tipo],
        parser=PARSER_BENEFICIO,
        campos={
            "anio_gravable": ext.anio_gravable,
            "tipo_beneficio": tipo.value,
            "entidad_nit": ext.entidad_nit,
            "entidad_nombre": ext.entidad,
            "valor": monto.valor,
            "certificada": ext.certificada,
        },
        confianza=monto.fuente.confianza or 0.0,
    )


class LlmReaderDeBeneficio(Protocol):
    """Firma del lector generado: el `doc_type` esperado va cerrado adentro."""

    def __call__(
        self, content: bytes, *, anio_esperado: int | None = ..., client: Any = ...
    ) -> DocumentReading: ...


def _lector_de_beneficio(doc_type: str) -> LlmReaderDeBeneficio:
    """El lector del registry para un beneficio: cierra sobre su `doc_type` esperado."""

    def leer(
        content: bytes, *, anio_esperado: int | None = None, client: Any = None
    ) -> DocumentReading:
        return _leer_beneficio(
            content, doc_type=doc_type, anio_esperado=anio_esperado, client=client
        )

    leer.__name__ = f"leer_{doc_type.lower()}"
    leer.__doc__ = f"Lee un {doc_type}."
    return leer


# Un lector por tipo, todos sobre el mismo extractor. Se arman acá y no a mano para que
# agregar un beneficio sea una línea en `TipoBeneficio` y su entrada en `DOC_TYPE_POR_TIPO`.
LECTORES_DE_BENEFICIO = {
    doc_type: _lector_de_beneficio(doc_type) for doc_type in TIPO_POR_DOC_TYPE
}

__all__ = [
    "LECTORES_DE_BENEFICIO",
    "TipoBeneficio",
    "leer_220",
    "leer_arriendo",
    "leer_bancario",
    "leer_dividendos",
    "leer_pension",
]
