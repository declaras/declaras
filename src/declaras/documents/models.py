"""Modelo uniforme de lectura de documentos.

Todo documento, venga del portal o de una foto del cliente, se lee al mismo modelo. Eso
permite que el motor tributario y la consola del contador traten cualquier documento igual,
y que cada valor sea auditable: siempre se sabe de que celda o de que fragmento salio.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Confidence:
    """Niveles de confianza de referencia.

    Un parser deterministico lee una celda concreta y no se equivoca, asi que reporta
    certeza total. Un modelo de vision estima, y su confianza es la que el propio modelo
    declare.
    """

    DETERMINISTIC = 1.0
    LOW = 0.5


class ExtractedField(BaseModel):
    """Un valor escalar leido de un documento, con su procedencia."""

    name: str
    value: Any
    confidence: float = Field(default=Confidence.DETERMINISTIC, ge=0.0, le=1.0)
    # Celda del XLSX, casilla del formulario o fragmento de texto que lo respalda.
    source: str | None = None
    unit: str | None = None


class ExtractedRow(BaseModel):
    """Una fila de una tabla del documento."""

    values: dict[str, Any]
    source: str | None = None


class ReadingWarning(BaseModel):
    """Algo que hay que mirar al leer un documento, sin que sea una falla.

    `needs_action` distingue dos cosas que no se parecen: un aviso que le pide a alguien hacer
    algo (confirmar un valor, volver a pedir un archivo) y una constancia de un defecto conocido
    que no cambia ninguna cifra. Solo el lector sabe cual de las dos esta emitiendo, asi que lo
    declara aqui en vez de dejar que cada consumidor lo adivine.

    Importa porque un aviso que dice "esto no afecta nada" y que igual aparece en la lista de
    pendientes ensucia la lista y le quita autoridad a los que si hay que atender.
    """

    code: str
    message: str
    source: str | None = None
    needs_action: bool = True


class DocumentReading(BaseModel):
    """Resultado de leer un documento."""

    doc_type: str
    parser: str
    content_sha256: str
    fields: list[ExtractedField] = Field(default_factory=list)
    rows: list[ExtractedRow] = Field(default_factory=list)
    warnings: list[ReadingWarning] = Field(default_factory=list)

    def field(self, name: str) -> Any:
        """Valor de un campo por nombre, o None si no se leyo."""
        for item in self.fields:
            if item.name == name:
                return item.value
        return None


class ThresholdCode(StrEnum):
    """Los cinco topes con que la DIAN determina si alguien esta obligado a declarar."""

    INGRESOS = "ingresos"
    PATRIMONIO = "patrimonio"
    CONSUMO_TARJETA = "consumo_tarjeta"
    MOVIMIENTOS = "movimientos"
    COMPRAS = "compras"
