"""Endpoints de lectura de documentos.

Dos entradas para la misma operacion. `read` recibe bytes directos: es lo que llama el
agente conversacional cuando el cliente manda una foto o un PDF por el chat. `read_stored`
recibe la referencia de un documento que el conector DIAN ya descargo y almacenado: es lo
que encadena la extraccion del portal con la lectura estructurada, sin bajar el archivo
dos veces.

Es sincronico a proposito (a diferencia de la extraccion DIAN): leer un documento toma
segundos, no minutos, asi que un job por cada lectura seria complejidad sin beneficio.

Sincronico para quien llama, pero NO en el hilo del event loop: `document_reader.read` es una
funcion bloqueante, y desde que hay lectores que llaman a un modelo puede tardar decenas de
segundos. Ejecutarla en el loop congelaria todas las demas requests y el worker de
extracciones, que es una task del mismo loop; por eso va en el threadpool.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

from declaras.api.deps import ApiKeyDep, ContainerDep
from declaras.api.schemas import DocumentReadingResponse, ReadStoredDocumentRequest
from declaras.documents.registry import supported_types
from declaras.documents.sniff import DESCONOCIDO, detectar_tipo
from declaras.domain.errors import ValidationError

router = APIRouter(prefix="/v1/documents", tags=["documents"])


@router.get(
    "/types",
    summary="Lista los tipos de documento que ya tienen lector",
)
async def list_supported_types(_auth: ApiKeyDep) -> list[str]:
    return supported_types()


@router.post(
    "/read",
    response_model=DocumentReadingResponse,
    summary="Lee un documento subido directamente (foto o PDF del cliente)",
)
async def read_document(
    container: ContainerDep,
    _auth: ApiKeyDep,
    doc_type: str | None = Form(
        None,
        description=(
            "Tipo de documento, ej. EXOGENA, RUT. Opcional: si falta, un modelo lo clasifica. "
            "Informarlo es preferible — el flujo del producto sabe qué documento pidió, y "
            "decirlo elimina una fuente de error."
        ),
    ),
    file: UploadFile = File(...),
    anio_esperado: int | None = Form(
        None,
        description=(
            "Año gravable que se está declarando. Los certificados que lee un modelo lo usan "
            "para rechazar el documento de otro año; sin él, ese chequeo no corre."
        ),
    ),
) -> DocumentReadingResponse:
    content = await file.read()
    if doc_type is None:
        # Adivinar cuesta una llamada al modelo y solo se hace cuando nadie dijo qué es. Va al
        # threadpool igual que la lectura: bloquea decenas de segundos y el event loop es
        # compartido con el runner de jobs.
        doc_type = await run_in_threadpool(detectar_tipo, content)
        if doc_type == DESCONOCIDO:
            # No se adivina un default: un certificado clasificado mal mete su cifra en el
            # renglón equivocado del formulario, y preguntar cuesta solo una pregunta.
            raise ValidationError(
                "No se pudo identificar qué documento es. Indica el tipo explícitamente.",
                supported=supported_types(),
            )
    reading = await run_in_threadpool(
        container.document_reader.read,
        content=content,
        doc_type=doc_type,
        anio_esperado=anio_esperado,
    )
    return DocumentReadingResponse.from_reading(reading)


@router.post(
    "/read-stored",
    response_model=DocumentReadingResponse,
    summary="Lee un documento que el conector DIAN ya descargo y almaceno",
)
async def read_stored_document(
    payload: ReadStoredDocumentRequest,
    container: ContainerDep,
    _auth: ApiKeyDep,
) -> DocumentReadingResponse:
    content = await container.store.read(payload.storage_uri)
    reading = await run_in_threadpool(
        container.document_reader.read, content=content, doc_type=payload.doc_type
    )
    return DocumentReadingResponse.from_reading(reading)
