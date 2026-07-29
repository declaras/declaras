from typing import Literal

from pydantic import BaseModel, ConfigDict

from declaras.motor.elecciones import Elecciones


class Nodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    etiqueta: str
    valor: int
    formula: str
    insumos: list[str] = []
    regla: str | None = None


class Flag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    mensaje: str
    severidad: Literal["info", "advertencia", "bloqueante"] = "advertencia"


class Liquidacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anio_gravable: int
    elecciones: Elecciones
    nodos: dict[str, Nodo]
    flags: list[Flag]

    def valor(self, codigo: str) -> int:
        return self.nodos[codigo].valor

    def tiene_flag(self, codigo: str) -> bool:
        return any(f.codigo == codigo for f in self.flags)


class Traza:
    """Acumulador del árbol de cálculo. Cada casilla queda con fórmula e insumos."""

    def __init__(self) -> None:
        self.nodos: dict[str, Nodo] = {}
        self.flags: list[Flag] = []

    def nodo(
        self,
        codigo: str,
        etiqueta: str,
        valor: int,
        formula: str,
        insumos=(),
        regla: str | None = None,
    ) -> int:
        if codigo in self.nodos:
            raise ValueError(f"Código de nodo duplicado: {codigo}")
        self.nodos[codigo] = Nodo(
            codigo=codigo,
            etiqueta=etiqueta,
            valor=valor,
            formula=formula,
            insumos=list(insumos),
            regla=regla,
        )
        return self.nodos[codigo].valor

    def flag(
        self,
        codigo: str,
        mensaje: str,
        severidad: Literal["info", "advertencia", "bloqueante"] = "advertencia",
    ) -> None:
        self.flags.append(Flag(codigo=codigo, mensaje=mensaje, severidad=severidad))

    def a_liquidacion(self, anio: int, elecciones: Elecciones) -> Liquidacion:
        return Liquidacion(
            anio_gravable=anio,
            elecciones=elecciones,
            nodos=dict(self.nodos),
            flags=list(self.flags),
        )
