"""Las dos operaciones del conciliador: `abrir` con la exógena e `incorporar` de a uno.

EL ORDEN TEMPORAL DEFINE LA API

Primero llega la exógena de la DIAN, completa y de una: `abrir` convierte cada grupo
(tercero, concepto) en una partida en su estado inicial. Después van llegando los documentos
del cliente, de a uno y con días de diferencia: `incorporar` cruza UN documento contra las
partidas que ya había. No hay operación por lotes porque cuando llega el primer certificado
los demás todavía no existen.

Las dos funciones son puras: no mutan sus entradas y devuelven la lista nueva, así que quien
persiste partidas decide cuándo reemplazar su copia.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from declaras.documents.models import DocumentReading
from declaras.services.conciliacion.conceptos import Concepto, concepto_de_codigo
from declaras.services.conciliacion.modelos import EstadoPartida, Lado, Partida, Valor

# Notas de una partida cuya fila de exógena no fue reportada al titular. Son texto que lee
# el contador; la MARCA con la que decide el código es `Partida.reportado_a`, que es
# estructural: una nota es texto libre que otras capas reescriben (el `refrescar` de T5 lo
# hace por spec), y una marca que viva ahí desaparece con la primera reescritura.
NOTA_OTRA_IDENTIFICACION = "reportado a otra identificación"
NOTA_OTRO_NOMBRE = (
    "reportado al número del titular pero a nombre de otra persona; "
    "hay que confirmar si es suyo"
)

_NOTA_SIN_NIT = "el documento no trae el NIT del tercero, así que no se pudo cruzar con la exógena"
_NOTA_CERTIFICADO_SIN_RESPALDO = (
    "llegó un certificado del tercero, pero no concilia contra un valor reportado a otra persona"
)


@dataclass(frozen=True)
class _ClaveDocumento:
    """Cómo se deriva de un tipo de documento la llave (NIT, concepto) y sus dos números."""

    concepto: Concepto
    campo_nit: str
    campo_nombre: str
    campos_monto: tuple[str, ...]
    campo_retencion: str


# doc_type → cómo cruzarlo. Tabla incremental, igual que la de códigos: un tipo que no esté
# acá no se cruza a ciegas, revienta en `incorporar` para que se decida su mapeo a conciencia.
TIPO_A_CLAVE: dict[str, _ClaveDocumento] = {
    "CERT_INGRESOS_220": _ClaveDocumento(
        concepto=Concepto.SALARIOS,
        campo_nit="empleador_nit",
        campo_nombre="empleador_nombre",
        # El concepto 5001 de la exógena agrega TODOS los pagos laborales del empleador, así
        # que el lado documento suma las casillas de pago del 220. Comparar solo `salarios`
        # marcaría discrepancia falsa a cualquiera que recibió prima o cesantías.
        campos_monto=("salarios", "cesantias_e_intereses", "prima", "bonificaciones"),
        campo_retencion="retencion",
    ),
}


# ─────────────────────────── fase 1: abrir con la exógena ───────────────────────────


@dataclass
class _Grupo:
    """Acumulador de las filas de exógena que comparten llave."""

    nit: str
    nombre: str
    clave: str
    concepto: Concepto | None
    # A quién le reportó el tercero cuando no fue al titular (None = sí fue al titular).
    reportado_a: str | None
    nota: str | None
    codigos: list[str] = field(default_factory=list)
    celdas: list[str] = field(default_factory=list)
    monto: int = 0
    retencion: int = 0


def abrir(exogena: DocumentReading) -> list[Partida]:
    """Fase 1: convierte la exógena en partidas. Todas nacen sin lado documento.

    Varias filas de la misma llave se suman (un tercero reporta el mismo concepto en varias
    filas), y cada grupo nace SOLO_DIAN — o CONCEPTO_DESCONOCIDO si su código no mapea.
    """
    titular = str(exogena.field("id_number") or "").strip()
    grupos: dict[tuple[str, str, bool], _Grupo] = {}
    for fila in exogena.rows:
        valores = fila.values
        nit = str(valores.get("reporter_nit") or "").strip()
        codigo = str(valores.get("concept_code") or "").strip()
        concepto = concepto_de_codigo(codigo)

        # Una fila que el tercero no le reportó al titular es un hecho DISTINTO, no una
        # discrepancia de montos: se agrupa aparte para no contaminar la suma de lo que sí
        # es del titular, y nunca aportará hecho.
        reportado_a, nota = _reportado_a(titular, valores)

        # Para un código sin mapear, la identidad del grupo es el código crudo: fusionar dos
        # códigos desconocidos distintos sería asumir que son el mismo hecho.
        clave = str(concepto) if concepto is not None else (codigo or "desconocido")
        grupo = grupos.setdefault(
            (nit, clave, reportado_a is not None),
            _Grupo(
                nit=nit,
                nombre=str(valores.get("reporter_name") or ""),
                clave=clave,
                concepto=concepto,
                reportado_a=reportado_a,
                nota=nota,
            ),
        )
        grupo.monto += int(valores.get("amount") or 0)
        grupo.retencion += int(valores.get("retencion") or 0)
        if codigo and codigo not in grupo.codigos:
            grupo.codigos.append(codigo)
        if fila.source:
            grupo.celdas.append(fila.source)
    return [_partida_dian(grupo) for grupo in grupos.values()]


def _reportado_a(titular: str, valores: dict[str, object]) -> tuple[str | None, str | None]:
    """A quién le reportó el tercero la fila si no fue al titular, y la nota que lo explica.

    El lector de exógena ya decidió esto fila por fila (`reported_to_titular`, que además
    cubre el caso "misma cédula, otro nombre" comparando los nombres normalizados) y su
    docstring pide explícitamente no volver a decidirlo. Se le hace caso; la comparación de
    números queda solo como respaldo para filas que no traigan la conclusión (lecturas
    hechas antes de que existiera o construidas a mano). Sin titular legible no se puede
    afirmar que una fila sea ajena, así que no se marca ninguna.
    """
    reportado_id = str(valores.get("reported_id_number") or "").strip()
    otra_identificacion = bool(titular and reportado_id and reportado_id != titular)
    if "reported_to_titular" in valores:
        if valores["reported_to_titular"]:
            return None, None
        if otra_identificacion:
            return reportado_id, NOTA_OTRA_IDENTIFICACION
        otro_nombre = str(valores.get("reported_name") or "").strip() or "otra persona"
        return otro_nombre, NOTA_OTRO_NOMBRE
    if otra_identificacion:
        return reportado_id, NOTA_OTRA_IDENTIFICACION
    return None, None


def _partida_dian(grupo: _Grupo) -> Partida:
    estado = (
        EstadoPartida.CONCEPTO_DESCONOCIDO
        if grupo.concepto is None
        else EstadoPartida.SOLO_DIAN
    )
    return Partida(
        # El contrato fija id = f"{nit}:{concepto}". Una partida ajena comparte id con su
        # gemela reportada al titular si ambas existieran; `incorporar` prefiere la del
        # titular al emparejar.
        id=f"{grupo.nit}:{grupo.clave}",
        nit_tercero=grupo.nit,
        nombre_tercero=grupo.nombre,
        concepto=grupo.concepto,
        codigos_crudos=list(grupo.codigos),
        version_dian=Valor(
            monto=grupo.monto,
            retencion=grupo.retencion,
            lado=Lado.DIAN,
            celda=", ".join(dict.fromkeys(grupo.celdas)) or None,
            # El lector de exógena es determinístico: leyó celdas, no estimó.
            confianza=1.0,
        ),
        estado=estado,
        nota=grupo.nota,
        reportado_a=grupo.reportado_a,
    )


# ─────────────────────────── fase 2: incorporar un documento ───────────────────────────


def incorporar(
    partidas: list[Partida],
    documento: DocumentReading,
    *,
    tolerancia_pesos: int = 1000,
) -> list[Partida]:
    """Fase 2: cruza UN documento contra las partidas y devuelve la lista actualizada.

    La partida que empareja por id cambia de estado (COINCIDE o DISCREPANCIA según la
    tolerancia, sobre monto Y retención); si ninguna empareja, nace una SOLO_DOCUMENTO.
    """
    clave = TIPO_A_CLAVE.get(documento.doc_type)
    if clave is None:
        # Error de programación del llamador, no un mensaje para el cliente: los tipos que
        # el conciliador sabe cruzar están en TIPO_A_CLAVE y se agregan a conciencia.
        raise ValueError(
            f"El conciliador no sabe cruzar documentos de tipo {documento.doc_type!r}."
        )

    version = _version_documento(documento, clave)
    nit = str(documento.field(clave.campo_nit) or "").strip()
    nombre = str(documento.field(clave.campo_nombre) or "").strip()

    if not nit:
        # Sin NIT no hay llave (entrada manual o lectura incompleta): nace suelta y con la
        # explicación, en vez de adivinar a qué tercero pertenece. El prefijo del sha es el
        # mismo identificador corto con que el expediente refiere el documento.
        suelta = Partida(
            id=f"{documento.content_sha256[:12]}:{clave.concepto}",
            nit_tercero="",
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            estado=EstadoPartida.SOLO_DOCUMENTO,
            nota=_NOTA_SIN_NIT,
        )
        return [*partidas, suelta]

    objetivo = _indice_emparejable(partidas, f"{nit}:{clave.concepto}")
    if objetivo is None:
        # La DIAN no conoce este hecho (todavía): el documento es la única versión.
        nueva = Partida(
            id=f"{nit}:{clave.concepto}",
            nit_tercero=nit,
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            estado=EstadoPartida.SOLO_DOCUMENTO,
        )
        return [*partidas, nueva]

    actualizadas = list(partidas)
    actualizadas[objetivo] = _emparejar(partidas[objetivo], version, tolerancia_pesos)
    return actualizadas


def _version_documento(documento: DocumentReading, clave: _ClaveDocumento) -> Valor:
    campos = {f.name: f for f in documento.fields}
    usados = [campos[n] for n in (*clave.campos_monto, clave.campo_retencion) if n in campos]
    retencion = campos.get(clave.campo_retencion)
    celdas = [c.source for c in usados if c.source]
    confianzas = [c.confidence for c in usados]
    return Valor(
        monto=sum(int(campos[n].value or 0) for n in clave.campos_monto if n in campos),
        retencion=int(retencion.value or 0) if retencion is not None else 0,
        lado=Lado.DOCUMENTO,
        # La procedencia viene de la lectura (`ExtractedField.source`/`.confidence`). La
        # confianza del agregado es la mínima de los campos usados: la suma no es más
        # confiable que su peor sumando.
        celda=", ".join(dict.fromkeys(celdas)) or None,
        confianza=min(confianzas) if confianzas else None,
    )


def _indice_emparejable(partidas: list[Partida], id_partida: str) -> int | None:
    """Contra qué partida cruza el documento, prefiriendo la reportada al titular.

    Pueden compartir id la partida del titular y una gemela que el tercero le reportó a otra
    identificación: el documento confirma a la del titular. La gemela solo se toma si es la
    única, para que el certificado quede a la vista en vez de perderse en silencio.
    """
    ajena: int | None = None
    for i, partida in enumerate(partidas):
        if partida.id != id_partida:
            continue
        if not _es_ajena(partida):
            return i
        if ajena is None:
            ajena = i
    return ajena


def _es_ajena(partida: Partida) -> bool:
    """La marca es el campo estructural, nunca el texto de la nota: `refrescar` de T5
    reescribe `nota` por spec, y una marca que viviera ahí desaparecería con él."""
    return partida.reportado_a is not None


def _emparejar(partida: Partida, version: Valor, tolerancia_pesos: int) -> Partida:
    if _es_ajena(partida):
        # La fila de la DIAN es de otra persona y NUNCA aporta hecho, así que tampoco puede
        # confirmar este certificado: la partida no cambia de estado. El documento queda
        # adjunto y anotado para que el contador vea las dos cosas y decida (puede resolver
        # con otro valor); descartarlo sería una pérdida silenciosa.
        nota = partida.nota or ""
        if _NOTA_CERTIFICADO_SIN_RESPALDO not in nota:
            prefijo = f"{nota}; " if nota else ""
            nota = prefijo + _NOTA_CERTIFICADO_SIN_RESPALDO
        return partida.model_copy(update={"version_documento": version, "nota": nota})

    dian = partida.version_dian
    if dian is None:
        # Emparejó contra una partida que ya era solo-documento: una lectura nueva del mismo
        # tercero reemplaza a la anterior (el caso real es un reenvío corregido) y sigue sin
        # haber contra qué comparar.
        return partida.model_copy(
            update={"version_documento": version, "estado": EstadoPartida.SOLO_DOCUMENTO}
        )

    coincide = (
        abs(dian.monto - version.monto) <= tolerancia_pesos
        and abs(dian.retencion - version.retencion) <= tolerancia_pesos
    )
    return partida.model_copy(
        update={
            "version_documento": version,
            "estado": EstadoPartida.COINCIDE if coincide else EstadoPartida.DISCREPANCIA,
        }
    )
