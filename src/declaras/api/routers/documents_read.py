"""Endpoints de lectura de documentos.

Dos entradas para la misma operacion. `read` recibe bytes directos: es lo que llama el
agente conversacional cuando el cliente manda una foto o un PDF por el chat. `read_stored`
recibe la referencia de un documento que el conector DIAN ya descargo y almacenado: es lo
que encadena la extraccion del portal con la lectura estructurada, sin bajar el archivo
dos veces.

Es sincronico a proposito (a diferencia de la extraccion DIAN): leer un documento toma
segundos, no minutos, asi que un job por cada lectura seria complejidad sin beneficio.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from declaras.api.deps import ApiKeyDep, ContainerDep
from declaras.api.schemas import DocumentReadingResponse, ReadStoredDocumentRequest
from declaras.documents.registry import supported_types

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
    doc_type: str = Form(..., description="Tipo de documento, ej. EXOGENA, RUT"),
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
    reading = container.document_reader.read(
        content=content, doc_type=doc_type, anio_esperado=anio_esperado
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
    reading = container.document_reader.read(content=content, doc_type=payload.doc_type)
    return DocumentReadingResponse.from_reading(reading)
