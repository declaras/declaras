"""Descarga de los documentos extraidos.

Si el backend de almacenamiento soporta URLs firmadas (GCS), se redirige alla para no
pasar los bytes por la API. Con disco local se sirve el contenido directamente.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, Response

from declaras.api.deps import ApiKeyDep, ContainerDep

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_SIGNED_URL_TTL_S = 900


@router.get(
    "/content",
    summary="Descarga el contenido de un documento extraido",
    response_class=Response,
)
async def download_document(
    container: ContainerDep,
    _auth: ApiKeyDep,
    uri: str = Query(..., description="storage_uri devuelto en el resultado de la extraccion"),
) -> Response:
    signed = await container.store.signed_url(uri, _SIGNED_URL_TTL_S)
    if signed:
        return RedirectResponse(signed, status_code=307)

    content = await container.store.read(uri)
    filename = uri.rsplit("/", 1)[-1]
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
