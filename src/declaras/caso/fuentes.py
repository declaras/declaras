from typing import Literal

from pydantic import BaseModel


class Fuente(BaseModel):
    """Proveniencia de un hecho: de dónde salió y con qué confianza."""

    clase: Literal["documento", "manual", "fixture", "exogena"]
    ref: str
    detalle: str | None = None
    confianza: float | None = None

    @classmethod
    def manual(cls, quien: str) -> "Fuente":
        return cls(clase="manual", ref=quien)

    @classmethod
    def fixture(cls, nombre: str) -> "Fuente":
        return cls(clase="fixture", ref=nombre)

    @classmethod
    def documento(cls, tipo: str, doc_id: str, pagina: int | None = None,
                  confianza: float | None = None) -> "Fuente":
        detalle = f"{tipo} pág {pagina}" if pagina else tipo
        return cls(clase="documento", ref=doc_id, detalle=detalle, confianza=confianza)


class MontoDeclarado(BaseModel):
    valor: int
    fuente: Fuente
