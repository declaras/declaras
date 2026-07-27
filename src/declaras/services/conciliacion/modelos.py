"""Los modelos del conciliador: la partida, sus dos versiones y sus cinco desenlaces.

Una partida es UN hecho económico entre un tercero y el contribuyente —la llave es el NIT
del tercero más el concepto normalizado— visto desde los dos lados que pueden contarlo: lo
que el tercero le reportó a la DIAN (exógena) y lo que dice el documento que entregó el
cliente. Conciliar es comparar esas dos versiones número a número.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from declaras.services.conciliacion.conceptos import Concepto


class _Modelo(BaseModel):
    """Una clave desconocida revienta en vez de descartarse (misma regla que en `caso`)."""

    model_config = ConfigDict(extra="forbid")


class Lado(StrEnum):
    """De cuál de las dos realidades viene un valor."""

    DIAN = "DIAN"
    DOCUMENTO = "DOCUMENTO"


class EstadoPartida(StrEnum):
    """Los cinco desenlaces posibles de una partida."""

    # Las dos versiones existen y sus números cierran dentro de la tolerancia.
    COINCIDE = "COINCIDE"
    # Las dos versiones existen y algún número (monto o retención) no cierra.
    DISCREPANCIA = "DISCREPANCIA"
    # Solo está el lado DIAN: falta el documento del cliente, o la fila es de otra persona.
    SOLO_DIAN = "SOLO_DIAN"
    # Solo está el documento: la DIAN no conoce (todavía) este hecho.
    SOLO_DOCUMENTO = "SOLO_DOCUMENTO"
    # La exógena trae un código que la tabla no mapea: pregunta al contador, no un default.
    CONCEPTO_DESCONOCIDO = "CONCEPTO_DESCONOCIDO"


class Valor(_Modelo):
    """Lo que un lado afirma: los dos números que se comparan, con su procedencia."""

    monto: int
    retencion: int
    lado: Lado
    # Celda del XLSX o fragmento del documento que respalda el valor (el `source` de la
    # lectura). Es lo que después se traduce a `Fuente.celda` cuando el hecho entra al caso.
    celda: str | None = None
    confianza: float | None = Field(default=None, ge=0.0, le=1.0)


class Partida(_Modelo):
    """Un hecho económico entre un tercero y el contribuyente, visto desde los dos lados.

    `resolucion: Resolucion | None` llega con las resoluciones del contador (la siguiente
    tarea del plan define `Resolucion` en este mismo módulo); declararla hoy obligaría a
    inventar un modelo que esa tarea ya especifica completo.
    """

    # Estable: f"{nit}:{concepto}" para conceptos conocidos. Es la referencia con que una
    # resolución del contador vuelve a la discrepancia que la originó.
    id: str
    nit_tercero: str
    nombre_tercero: str
    # None = código sin mapear; el concepto NUNCA se asume.
    concepto: Concepto | None
    # Los códigos oficiales tal como vinieron: dos códigos que normalizan al mismo concepto
    # (5002 y 5003) son una sola partida, y acá queda el rastro de cuáles fueron.
    codigos_crudos: list[str] = Field(default_factory=list)
    version_dian: Valor | None = None
    version_documento: Valor | None = None
    estado: EstadoPartida
    nota: str | None = None

    @property
    def diferencia_monto(self) -> int:
        """Cuánta plata separa las dos versiones; 0 si falta un lado (nada que comparar)."""
        if self.version_dian is None or self.version_documento is None:
            return 0
        return abs(self.version_dian.monto - self.version_documento.monto)

    @property
    def diferencia_retencion(self) -> int:
        """La retención se expone aparte del monto: declarar más retención de la que el
        tercero reportó casi garantiza un requerimiento de la DIAN."""
        if self.version_dian is None or self.version_documento is None:
            return 0
        return abs(self.version_dian.retencion - self.version_documento.retencion)
