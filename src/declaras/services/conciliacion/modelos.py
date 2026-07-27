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
    # Solo el lado DIAN sostiene el hecho: falta el documento del cliente, o la fila es de
    # otra persona (`reportado_a`), caso en el que puede tener un certificado adjunto en
    # `version_documento` SIN que eso concilie nada — un certificado del titular no puede
    # confirmarse contra una fila que la DIAN le reportó a otra persona.
    SOLO_DIAN = "SOLO_DIAN"
    # Solo está el documento: la DIAN no conoce (todavía) este hecho.
    SOLO_DOCUMENTO = "SOLO_DOCUMENTO"
    # La exógena trae un código que la tabla no mapea: pregunta al contador, no un default.
    CONCEPTO_DESCONOCIDO = "CONCEPTO_DESCONOCIDO"


class Valor(_Modelo):
    """Lo que un lado afirma: los dos números que se comparan, con su procedencia."""

    monto: int
    # None = este lado NO reportó la retención. No es lo mismo que 0: el XLSX real de la
    # exógena no trae columna de retención, y tratar "no reportada" como 0 hacía imposible
    # que un asalariado real quedara COINCIDE (discrepancia falsa del tamaño de toda su
    # retención). Desviación deliberada del brief (`retencion: int`), autorizada en la
    # ronda de fixes 1 de la T4: el brief se escribió antes de saber qué columnas trae el
    # lector. Un agente futuro no debe "corregirla" de vuelta a `int`.
    retencion: int | None
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

    TRAMPA VERIFICADA para quien escriba esa tarea: `model_copy(update={...})` NO respeta
    `extra="forbid"` — acepta claves que no existen en el modelo y las descarta del
    `model_dump()` sin error. Un `update={"resolucion": r}` escrito ANTES de que el campo
    exista no revienta: pierde la resolución en silencio. Agregar el campo primero.
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
    # El AGREGADO de los aportes de abajo (o None si no ha llegado documento): contra esto
    # se compara el lado DIAN, porque la exógena también agrega (un banco reporta la suma
    # de sus CDT en una fila).
    version_documento: Valor | None = None
    # El aporte de cada documento, llaveado por su identificador corto (sha[:12], el mismo
    # del expediente). Distingue "el mismo documento otra vez" (reemplaza su aporte: un
    # reenvío corregido) de "otro documento del mismo pagador" (suma: un certificado por
    # CDT) — sin esto, cada segundo certificado del mismo NIT borraba al anterior en
    # silencio y el resultado dependía del orden de llegada. Campo adicional al contrato
    # del plan, autorizado en la ronda de fixes 1 de la T4.
    versiones_documento: dict[str, Valor] = Field(default_factory=dict)
    estado: EstadoPartida
    nota: str | None = None
    # A quién le reportó el tercero cuando NO fue al titular: la otra identificación, o el
    # otro nombre cuando la cédula sí es la del titular. Es la marca ESTRUCTURAL de que la
    # fila de la DIAN no aporta hecho — no vive en `nota` (texto libre que otras capas
    # reescriben) ni en el estado. Campo adicional al contrato del plan, autorizado en la
    # ronda de fixes 1 de la T4.
    reportado_a: str | None = None

    @property
    def diferencia_monto(self) -> int:
        """Cuánta plata separa las dos versiones; 0 si falta un lado (nada que comparar).

        En una partida ajena (`reportado_a`) también es 0: la fila de la DIAN es de otra
        persona y el certificado es del titular, así que restar esos dos números no mide
        ninguna discrepancia real — y `pendientes` de T5 ordena por esta cifra.
        """
        if self.version_dian is None or self.version_documento is None:
            return 0
        if self.reportado_a is not None:
            return 0
        return abs(self.version_dian.monto - self.version_documento.monto)

    @property
    def diferencia_retencion(self) -> int:
        """La retención se expone aparte del monto: declarar más retención de la que el
        tercero reportó casi garantiza un requerimiento de la DIAN.

        Si un lado no la reportó (None) no hay diferencia que medir: 0.
        """
        if self.version_dian is None or self.version_documento is None:
            return 0
        if self.reportado_a is not None:
            return 0
        if self.version_dian.retencion is None or self.version_documento.retencion is None:
            return 0
        return abs(self.version_dian.retencion - self.version_documento.retencion)
