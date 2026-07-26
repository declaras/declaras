from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Debajo de esto un valor extraído se usa igual, pero queda marcado para revisión humana:
# el número entra a un formulario tributario. El umbral es del concepto
# `Fuente.confianza`, así que vive acá y lo comparten las dos capas que avisan: el API al
# subir el documento y el motor al validar el caso. Con una constante por capa, subirle el
# umbral a una dejaba la otra callada.
CONFIANZA_MINIMA = 0.7


class _Modelo(BaseModel):
    """Base de todo el caso: una clave desconocida revienta en vez de descartarse.

    Sin esto, un typo en el body del API (`salariosss`) se ignora en silencio y el
    campo real vuelve a su default en cada escritura.
    """

    model_config = ConfigDict(extra="forbid")


class Fuente(_Modelo):
    """Proveniencia de un hecho: de dónde salió y con qué confianza."""

    clase: Literal["documento", "manual", "fixture", "exogena"]
    ref: str
    detalle: str | None = None
    confianza: float | None = Field(default=None, ge=0.0, le=1.0)

    @classmethod
    def manual(cls, quien: str) -> "Fuente":
        return cls(clase="manual", ref=quien)

    @classmethod
    def fixture(cls, nombre: str) -> "Fuente":
        return cls(clase="fixture", ref=nombre)

    @classmethod
    def documento(cls, tipo: str, doc_id: str, pagina: int | None = None,
                  confianza: float | None = None) -> "Fuente":
        detalle = f"{tipo} pág {pagina}" if pagina is not None else tipo
        return cls(clase="documento", ref=doc_id, detalle=detalle, confianza=confianza)


class MontoDeclarado(_Modelo):
    valor: int = Field(ge=0)
    fuente: Fuente
