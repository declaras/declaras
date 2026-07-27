"""Los modelos del conciliador: la partida, sus dos versiones y sus cinco desenlaces.

Una partida es UN hecho económico entre un tercero y el contribuyente —la llave es el NIT
del tercero más el concepto normalizado— visto desde los dos lados que pueden contarlo: lo
que el tercero le reportó a la DIAN (exógena) y lo que dice el documento que entregó el
cliente. Conciliar es comparar esas dos versiones número a número.
"""

from datetime import datetime
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
    # otra persona (`reportado_a`). Un certificado del titular NUNCA se le adjunta a una
    # ajena — es evidencia sobre el titular y no puede vivir dentro de una partida que
    # dice "esto no es del titular" y cuyas diferencias van forzadas a 0 —: abre su propia
    # partida SOLO_DOCUMENTO y la ajena guarda la marca en `documentos_por_cruzar`.
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
    # El nombre del tercero tal como ESTE lado lo afirma (None si no lo trae). En el
    # camino sin NIT es el único dato con que el contador puede decidir si dos versiones
    # rivales son el mismo certificado repetido o dos terceros distintos (ruling de F1):
    # por eso viaja POR VERSIÓN y no solo en `Partida.nombre_tercero`, que muestra el de
    # la versión publicada. En un agregado: el nombre común, o None si difieren.
    tercero: str | None = None
    # Celda del XLSX o fragmento del documento que respalda el valor (el `source` de la
    # lectura). Es lo que después se traduce a `Fuente.celda` cuando el hecho entra al caso.
    celda: str | None = None
    confianza: float | None = Field(default=None, ge=0.0, le=1.0)


class Decision(StrEnum):
    """Lo que se decidió hacer con la partida: de dónde sale la cifra que se declara.

    `MARCAR_AJENO`, `CERRAR_SIN_SOPORTE` y `LLEVAR_A_MANO` NO aportan hecho al caso (su
    `valor` queda en 0); las otras tres sí. Qué decisión es posible sobre qué estado lo
    valida `resolver`. `CERRAR_SIN_SOPORTE` y `LLEVAR_A_MANO` son cosas DISTINTAS:
    en la primera el soporte no existe (o el hecho ya está contado en otra partida);
    en la segunda el soporte existe y la cifra se conoce — lo que falta es el MOTOR
    (concepto en `CONCEPTOS_FUERA_DEL_MOTOR`), el contador suma ese ingreso a mano y la
    exclusión sale como aviso BLOQUEANTE en el borrador, porque excluir un ingreso es
    subdeclarar y nadie puede presentar ese 210 creyendo que está completo.
    """

    USAR_DIAN = "USAR_DIAN"
    USAR_DOCUMENTO = "USAR_DOCUMENTO"
    USAR_OTRO = "USAR_OTRO"
    MARCAR_AJENO = "MARCAR_AJENO"
    CERRAR_SIN_SOPORTE = "CERRAR_SIN_SOPORTE"
    LLEVAR_A_MANO = "LLEVAR_A_MANO"


class Motivo(StrEnum):
    """Por qué se tomó la decisión: es lo que el contador (o un auditor) lee después."""

    COINCIDEN = "COINCIDEN"
    ERROR_DEL_TERCERO = "ERROR_DEL_TERCERO"
    ERROR_DEL_CERTIFICADO = "ERROR_DEL_CERTIFICADO"
    NO_ES_MIO = "NO_ES_MIO"
    FALTA_DOCUMENTO = "FALTA_DOCUMENTO"
    DECISION_DEL_CONTADOR = "DECISION_DEL_CONTADOR"
    # El soporte y la cifra existen; lo que falta es el motor (va con LLEVAR_A_MANO).
    FUERA_DEL_MOTOR = "FUERA_DEL_MOTOR"
    # El documento es la fuente autoritativa del hecho y la exógena NO puede corroborarlo,
    # porque el tercero lo reporta bajo otro NIT. Es el motivo del tercer automatismo (los
    # aportes obligatorios de un 220, ver `autorresolver`): no es "coinciden" —no hay nada
    # que comparar— ni "decisión del contador" —no la tomó una persona—, y usar cualquiera
    # de los dos volvería a poner en la `Fuente` que lee un auditor una pareja
    # decisión/motivo que se contradice, que es justo lo que el M1 de la ronda 2 cerró.
    SIN_CONTRAPARTE_DIAN = "SIN_CONTRAPARTE_DIAN"


class Origen(StrEnum):
    """Quién resolvió: el peso de la resolución cuando algo cambia.

    Una de SISTEMA es provisional (existe para que el 210 preliminar exista sin esperar
    documentos) y `refrescar` la reemplaza SIEMPRE; una de CONTADOR es la decisión de una
    persona y solo sobrevive si su `huella` sigue coincidiendo con lo que hay.
    """

    SISTEMA = "SISTEMA"
    CONTADOR = "CONTADOR"


class Resolucion(_Modelo):
    """El desenlace que una persona (o el automatismo) le dio a una partida.

    `valor` es el monto en pesos que la resolución hace valer (0 cuando la decisión no
    aporta hecho). `huella` es el hash de las cifras de las dos versiones que el
    resolvedor vio al decidir (`resolucion._huella`): si al refrescar la huella ya no
    coincide, los valores cambiaron desde la resolución y la partida vuelve a pendiente.
    """

    decision: Decision
    valor: int
    motivo: Motivo
    origen: Origen
    huella: str
    nota: str | None = None
    quien: str
    cuando: datetime


class Partida(_Modelo):
    """Un hecho económico entre un tercero y el contribuyente, visto desde los dos lados.

    TRAMPA VERIFICADA: `model_copy(update={...})` NO respeta `extra="forbid"` — acepta
    claves que no existen en el modelo y las descarta del `model_dump()` sin error. Un
    typo en la clave de un `update` es un no-op silencioso: las transiciones de estado
    del conciliador escriben las claves literales y los tests fijan los valores.

    SIN VALIDADORES DE COHERENCIA entre campos, medido en el cierre de T4: pydantic
    acepta estados que `abrir`/`incorporar` nunca producen — un COINCIDE sin versiones,
    una ajena con las dos versiones adjuntas, un `version_que_rige` que no está en
    `versiones_documento`. Son inalcanzables por el cruce pero SÍ construibles a mano.
    Decisión de T5: NO se endurecen con `model_validator` — los tests del cruce
    construyen esos estados a propósito como defensas (p. ej. el guard `reportado_a` →
    diferencias 0), y las fábricas de los tests construyen vía `abrir`/`incorporar`, que
    es la única garantía real de coherencia. Los guards de negocio viven donde se usa la
    partida: `resolver` valida decisión contra estado, y `a_caso` rechaza hechos sin
    concepto y aportes sin ingreso.
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
    # del expediente): los mismos bytes otra vez son NO-OP (un retry de ingesta o un
    # reenvío del cliente no puede mover la cifra declarada), y un sha nuevo se GUARDA
    # siempre — nada desaparece en silencio. El contrato es de MEMBRESÍA, no de orden:
    # 'versión nueva vs. reenvío viejo' se decide por si el sha ya está acá, y qué
    # documento respalda la cifra publicada lo dice `version_que_rige`. Después de
    # persistir NADIE debe leer 'la última clave es la más nueva': JSONB no preserva el
    # orden de claves de un objeto (las reordena por longitud y luego bytes; medido contra
    # Postgres 17.10 en el cierre de T4) — la semántica del cruce da 0 diferencias bajo
    # esos reórdenes justamente porque no se apoya en el orden. Lo que se publica arriba
    # depende del tipo de documento (`acumulable` en TIPO_A_CLAVE): la suma cuando el tipo
    # emite varios por tercero de verdad (un certificado por CDT, y solo con NIT), o la
    # última versión nueva con nota cuando no (el sha es identidad de bytes, no de
    # documento: el mismo 220 re-escaneado llega con otro hash y sumarlo duplicaría la
    # plata). Campo adicional al contrato del plan, autorizado en la ronda de fixes 1.
    versiones_documento: dict[str, Valor] = Field(default_factory=dict)
    # El sha corto del documento cuya versión respalda la cifra publicada en
    # `version_documento` — SIEMPRE que lo publicado sea la versión de UN documento: una
    # sola, varias con las mismas cifras, o rivales (rige la última NUEVA en llegar;
    # reprocesar bytes ya vistos es no-op y no la cambia). None SOLO cuando lo publicado
    # es el agregado acumulable de varios documentos: ahí el respaldo es el conjunto
    # completo de `versiones_documento` y la procedencia publicada ya es la unión de sus
    # celdas — por eso no hace falta un ordinal en `Valor` ni pasar las versiones a un
    # array (la opción se evaluó en el cierre de T4 y se descartó: cambiaba el contrato
    # para conservar un orden en el que la semántica no se apoya). Antes solo se fijaba
    # con rivales, y con versiones de cifras iguales la procedencia publicada (celda,
    # confianza) apuntaba a un documento imposible de identificar después de persistir en
    # JSONB. OJO: "hubo rivales" NO se infiere de este campo — se deriva de las versiones
    # (más de una con cifras distintas, o más de una sin NIT) y la huella dura sigue
    # siendo `versiones_documento`. Es ESTRUCTURAL por la misma lección de `reportado_a`:
    # la nota es texto libre que `refrescar` de T5 sobrescribe, y la respuesta a "por qué
    # se declaró esta cifra" tiene que quedar en la partida. Campo adicional al contrato
    # del plan, autorizado en la ronda de fixes 3 y redefinido en el cierre de T4.
    version_que_rige: str | None = None
    estado: EstadoPartida
    nota: str | None = None
    # A quién le reportó el tercero cuando NO fue al titular: la otra identificación, o el
    # otro nombre cuando la cédula sí es la del titular. Es la marca ESTRUCTURAL de que la
    # fila de la DIAN no aporta hecho — no vive en `nota` (texto libre que otras capas
    # reescriben) ni en el estado. Campo adicional al contrato del plan, autorizado en la
    # ronda de fixes 1 de la T4.
    reportado_a: str | None = None
    # Marca estructural de la partida AJENA: shas cortos de los documentos del mismo
    # tercero y concepto que llegaron sin poder cruzarse contra ella (la fila es de otra
    # persona y no puede confirmarlos; cada documento abrió su propia partida
    # SOLO_DOCUMENTO). Nada se pierde: el contador ve acá que llegó un certificado que
    # podría corresponderle y lo cruza a mano. No vive en `nota` —texto libre que
    # `refrescar` de T5 reescribe por spec (la lección de I5)—. Campo adicional al
    # contrato del plan, autorizado en la ronda de fixes 4 de la T4.
    documentos_por_cruzar: list[str] = Field(default_factory=list)
    # None = pendiente de resolver. La ponen `resolver` (una persona) y `autorresolver`
    # (el sistema, provisional); `refrescar` decide si sobrevive cuando llegan datos
    # nuevos. Una partida cuyo id desaparece entre consultas (ids inestables documentados
    # en `_Grupo.id`) NO transfiere su resolución a ningún lado: la partida nueva nace
    # con None y vuelve a la cola — pendiente de nuevo, nunca resuelta por arrastre.
    resolucion: Resolucion | None = None

    @property
    def diferencia_monto(self) -> int:
        """Cuánta plata separa las dos versiones; 0 si falta un lado (nada que comparar).

        En una partida ajena (`reportado_a`) también es 0: la fila de la DIAN es de otra
        persona y el certificado es del titular, así que restar esos dos números no mide
        ninguna discrepancia real — y `pendientes` de T5 ordena por esta cifra. El cruce
        ya no adjunta documentos a una ajena (abren su propia partida); el guard queda
        como defensa para quien construya la partida por fuera.
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
