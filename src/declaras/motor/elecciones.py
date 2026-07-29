from pydantic import BaseModel, ConfigDict


class Elecciones(BaseModel):
    """Decisiones legales abiertas que el optimizador enumera."""

    model_config = ConfigDict(extra="forbid")

    usar_387: bool = False  # 10% art. 387 (dentro del límite 40%)
    usar_72uvt: bool = True  # 72 UVT por dependiente (extra-límite)

    @property
    def activas(self) -> int:
        return int(self.usar_387) + int(self.usar_72uvt)
