"""Convencion de rutas de almacenamiento.

El numero de documento no viaja en claro en la ruta: se usa un hash estable para no
esparcir datos personales por el bucket. La trazabilidad se mantiene por el job, que
si guarda la referencia del contribuyente.
"""

from __future__ import annotations

import hashlib
import mimetypes
from uuid import UUID

from declaras.domain.models import DocumentType, TaxpayerRef

_SUBJECT_HASH_LEN = 16


def subject_dir(taxpayer: TaxpayerRef) -> str:
    digest = hashlib.sha256(taxpayer.subject_key.encode()).hexdigest()[:_SUBJECT_HASH_LEN]
    return f"{taxpayer.id_kind.value.lower()}-{digest}"


def extension_for(filename: str, content_type: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    guessed = mimetypes.guess_extension(content_type) or ".bin"
    return guessed.lstrip(".")


def object_key(
    *,
    taxpayer: TaxpayerRef,
    doc_type: DocumentType,
    sha256: str,
    filename: str,
    content_type: str,
    job_id: UUID,
) -> str:
    ext = extension_for(filename, content_type)
    parts = [
        subject_dir(taxpayer),
        str(taxpayer.tax_year),
        doc_type.value.lower(),
    ]
    if doc_type is DocumentType.EVIDENCE:
        parts.append(str(job_id))
    parts.append(f"{sha256[:12]}.{ext}")
    return "/".join(parts)
