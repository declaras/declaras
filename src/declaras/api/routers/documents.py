"""Entrega de los documentos extraidos, para descargar o para ver sin descargar.

Si el backend de almacenamiento soporta URLs firmadas (GCS), se redirige alla para no pasar
los bytes por la API. Con disco local se sirve el contenido directamente.

DOS FORMAS DE ENTREGAR EL MISMO ARCHIVO

Descargar y ver son cosas distintas. Para revisar si el documento es el correcto, bajarlo al
disco y abrirlo con otro programa es una friccion innecesaria; para guardarlo, es justo lo que
se quiere. La diferencia esta en dos cabeceras, asi que el mismo endpoint hace las dos: hay
que declarar el tipo real del archivo (no `octet-stream`, que el navegador solo sabe bajar) y
pedir `inline` cuando se va a mostrar.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, Response

from declaras.api.deps import ApiKeyDep, ContainerDep

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_SIGNED_URL_TTL_S = 900

# Tipos que el navegador sabe mostrar sin ayuda. Los demas se entregan como binario, que es
# lo honesto: es mejor que el navegador ofrezca guardarlo que intentar mostrar algo ilegible.
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
}
_DEFAULT_MEDIA_TYPE = "application/octet-stream"

# El nombre con que se guarda el archivo viaja por la URL, asi que llega del cliente y no puede
# entrar tal cual en una cabecera de la respuesta: un salto de linea ahi permitiria inyectar
# otras cabeceras.
# En vez de escapar lo peligroso se conserva solo lo seguro, que para un nombre de archivo
# alcanza de sobra.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9 ._-]+")
_MAX_FILENAME_LEN = 120


def safe_filename(proposed: str | None, fallback: str) -> str:
    """Nombre de archivo utilizable, o el del almacenamiento si el propuesto no sirve."""
    if not proposed:
        return fallback
    clean = _UNSAFE_IN_FILENAME.sub("", proposed).strip(" .")[:_MAX_FILENAME_LEN]
    return clean or fallback


def media_type_for(filename: str) -> str:
    """Tipo de contenido segun la extension del archivo."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MEDIA_TYPES.get(extension, _DEFAULT_MEDIA_TYPE)


# El nombre del archivo va en la ruta, no solo en una cabecera, porque el visor de PDF del
# navegador titula el documento con el ultimo segmento de la URL: sin esto, quien abre su
# declaracion ve un documento llamado "content".
@router.get(
    "/content",
    summary="Entrega el contenido de un documento extraido, para descargar o para ver",
    response_class=Response,
)
@router.get("/content/{display_name}", include_in_schema=False, response_class=Response)
async def download_document(
    container: ContainerDep,
    _auth: ApiKeyDep,
    uri: str = Query(..., description="storage_uri devuelto en el resultado de la extraccion"),
    inline: bool = Query(False, description="Mostrar en el navegador en vez de descargar"),
    display_name: str | None = None,
) -> Response:
    signed = await container.store.signed_url(uri, _SIGNED_URL_TTL_S)
    if signed:
        return RedirectResponse(signed, status_code=307)

    content = await container.store.read(uri)
    # El almacenamiento nombra los archivos por hash, asi que sin esto alguien que descarga su
    # declaracion se queda con "e92f38b8ba15.pdf" en la carpeta de descargas.
    stored_name = uri.rsplit("/", 1)[-1]
    offered_name = safe_filename(display_name, stored_name)
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=media_type_for(stored_name),
        headers={"Content-Disposition": f'{disposition}; filename="{offered_name}"'},
    )
