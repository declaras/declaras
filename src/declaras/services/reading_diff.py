"""Comparacion de dos lecturas del mismo documento, para saber si una consulta trajo algo.

POR QUE EXISTE

Volver a consultar la DIAN es normal: la exogena se completa durante semanas y el borrador
sugerido cambia cuando un tercero corrige lo que reporto. Pero la DIAN incrusta la fecha de
generacion dentro de cada archivo, asi que el mismo documento descargado dos veces tiene
contenido distinto y hash distinto. Sin comparar lo que dice el documento, cada consulta
parece traer cinco documentos nuevos, y quien la hizo no puede distinguir "la DIAN publico
algo" de "no paso nada".

Comparar las lecturas, y no los archivos, es lo que permite decir "no hubo cambios" con
certeza en vez de con esperanza.

QUE SE IGNORA

La fecha en que la DIAN genero el reporte y su fecha de corte cambian en cada descarga sin
que cambie ni un peso de lo reportado. Para la pregunta "cambio algo que me afecte" son ruido,
asi que no cuentan como cambio. El texto completo del documento tampoco se compara: se guarda
para auditoria y arrastra la fecha de generacion.
"""

from __future__ import annotations

from pydantic import BaseModel

from declaras.documents.models import DocumentReading
from declaras.domain.models import document_label

# Campos que cambian en cada descarga sin que cambie lo reportado.
_VOLATILE_FIELDS = frozenset({"report_date", "cutoff_date", "raw_text"})


class FieldChange(BaseModel):
    """Un valor que cambio entre dos consultas."""

    name: str
    label: str
    before: object | None = None
    after: object | None = None


class ReadingDiff(BaseModel):
    """Lo que cambio en un documento entre dos consultas."""

    doc_type: str
    changed_fields: list[FieldChange] = []
    rows_before: int = 0
    rows_after: int = 0
    # Es la primera vez que se ve este documento en el expediente.
    is_new: bool = False

    @property
    def rows_changed(self) -> bool:
        return self.rows_before != self.rows_after

    @property
    def has_changes(self) -> bool:
        return self.is_new or bool(self.changed_fields) or self.rows_changed


def compare(
    *, doc_type: str, before: DocumentReading | None, after: DocumentReading | None
) -> ReadingDiff:
    """Compara dos lecturas del mismo tipo de documento.

    Sin lectura anterior es un documento nuevo. Sin lectura nueva no se puede afirmar nada:
    se reporta como sin cambios para no anunciar una actualizacion que no se pudo verificar.
    """
    if after is None:
        return ReadingDiff(doc_type=doc_type, is_new=before is None)
    if before is None:
        return ReadingDiff(doc_type=doc_type, is_new=True, rows_after=len(after.rows))

    previous = {f.name: f for f in before.fields if f.name not in _VOLATILE_FIELDS}
    current = {f.name: f for f in after.fields if f.name not in _VOLATILE_FIELDS}

    changes: list[FieldChange] = []
    for name in sorted(previous.keys() | current.keys()):
        antes, ahora = previous.get(name), current.get(name)
        valor_antes = antes.value if antes else None
        valor_ahora = ahora.value if ahora else None
        if valor_antes == valor_ahora:
            continue
        # La procedencia que reporta el lector es el nombre legible del dato (la celda o la
        # casilla de donde salio), asi que sirve para explicar el cambio sin traducir nada.
        presente = ahora or antes
        assert presente is not None  # el nombre viene de la union de ambos diccionarios
        changes.append(
            FieldChange(
                name=name,
                label=presente.source or name,
                before=valor_antes,
                after=valor_ahora,
            )
        )

    return ReadingDiff(
        doc_type=doc_type,
        changed_fields=changes,
        rows_before=len(before.rows),
        rows_after=len(after.rows),
    )


def describe_sync(diffs: list[ReadingDiff]) -> str:
    """Como contarle a una persona que trajo una consulta a la DIAN.

    Es la respuesta a la queja mas legitima que puede tener quien vuelve a consultar: si el
    sistema dice "se vincularon cinco documentos" cada vez, parece que algo se descargo de
    nuevo sin razon. Decir "no hubo cambios" solo se puede hacer si se verifico.
    """
    if not diffs:
        return "La consulta a la DIAN no trajo documentos"

    changed = [d for d in diffs if d.has_changes]
    if not changed:
        cuantos = "el documento" if len(diffs) == 1 else f"los {len(diffs)} documentos"
        return (
            f"La consulta a la DIAN no encontró cambios: {cuantos} siguen iguales a los de "
            "la consulta anterior"
        )

    if len(changed) == len(diffs) and all(d.is_new for d in changed):
        cuantos = "un documento" if len(diffs) == 1 else f"{len(diffs)} documentos"
        return f"La consulta a la DIAN trajo {cuantos}"

    nombres = [document_label(d.doc_type) for d in changed]
    listado = nombres[0] if len(nombres) == 1 else f"{', '.join(nombres[:-1])} y {nombres[-1]}"
    sin_cambios = len(diffs) - len(changed)
    cola = "; el resto sigue igual" if sin_cambios else ""
    verbo = "cambió" if len(changed) == 1 else "cambiaron"
    return f"La DIAN {verbo} {listado}{cola}"
