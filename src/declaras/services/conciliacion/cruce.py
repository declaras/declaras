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

import re
from dataclasses import dataclass, field

from declaras.dinero import pesos
from declaras.documents.models import DocumentReading, ExtractedField
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
_NOTA_RETENCION_AMBIGUA = (
    "la DIAN asigna esta fila a retenciones y a otro renglón a la vez; hay que clasificarla a mano"
)

# R132 del formulario 210: "Retenciones año gravable a declarar". Es el renglón con que la
# columna "Uso declaración Sugerida" marca las filas que son retención practicada, no ingreso.
_RENGLON_RETENCIONES = 132
_RENGLON_RE = re.compile(r"\bR(\d{1,3})\b")


@dataclass(frozen=True)
class _ClaveDocumento:
    """Cómo se deriva de un documento UNA llave (NIT, concepto) con sus dos números.

    Un mismo documento puede afirmar varios hechos: el 220 trae los pagos laborales Y los
    aportes obligatorios, y cada uno es su propia partida.
    """

    concepto: Concepto
    campo_nit: str
    campo_nombre: str
    campos_monto: tuple[str, ...]
    campo_retencion: str | None = None
    # Un hecho secundario del documento solo abre partida si trae plata: presente en 0 no
    # hay nada que perder ni pregunta que hacerle al contador.
    omitir_en_cero: bool = False


# doc_type → los hechos que ese documento afirma. Tabla incremental, igual que la de
# códigos: un tipo que no esté acá no se cruza a ciegas, revienta en `incorporar` para que
# su mapeo se decida a conciencia.
TIPO_A_CLAVE: dict[str, tuple[_ClaveDocumento, ...]] = {
    "CERT_INGRESOS_220": (
        _ClaveDocumento(
            concepto=Concepto.SALARIOS,
            campo_nit="empleador_nit",
            campo_nombre="empleador_nombre",
            # El concepto 5001 de la exógena agrega TODOS los pagos laborales del
            # empleador, así que el lado documento suma las casillas de pago del 220.
            # Comparar solo `salarios` marcaría discrepancia falsa a cualquiera que
            # recibió prima o cesantías.
            campos_monto=("salarios", "cesantias_e_intereses", "prima", "bonificaciones"),
            campo_retencion="retencion",
        ),
        # Los aportes obligatorios son hechos propios: `IngresoLaboral` los exige y sin
        # estas partidas la deducción se perdería (T5 solo podría armar el caso con 0).
        # El 220 del empleador es su fuente autoritativa; en la exógena la EPS/AFP los
        # reporta con su PROPIO NIT, así que esas filas no cruzan por el NIT del empleador
        # y corroboran bajo sus propias partidas.
        _ClaveDocumento(
            concepto=Concepto.APORTES_SALUD,
            campo_nit="empleador_nit",
            campo_nombre="empleador_nombre",
            campos_monto=("aportes_salud",),
            omitir_en_cero=True,
        ),
        _ClaveDocumento(
            concepto=Concepto.APORTES_PENSION,
            campo_nit="empleador_nit",
            campo_nombre="empleador_nombre",
            campos_monto=("aportes_pension",),
            omitir_en_cero=True,
        ),
    ),
}


# ─────────────────────────── fase 1: abrir con la exógena ───────────────────────────


@dataclass
class _Grupo:
    """Acumulador de las filas de exógena que comparten llave.

    LA LLAVE Y EL ID DERIVAN DEL MISMO DISCRIMINANTE COMPLETO (ref, clave, reportado_a):
    si el grupo separa por algo que el id no ve, nacen dos partidas con el mismo id y
    cualquier indexado (resoluciones, refrescar, Fuente.conciliacion) se come una de las
    dos según el orden del XLSX.
    """

    # Identidad del tercero: su NIT, o el respaldo de `_ref_tercero` cuando no lo trae.
    ref: str
    nit: str
    nombre: str
    clave: str
    concepto: Concepto | None
    # A quién le reportó el tercero cuando no fue al titular (None = sí fue al titular).
    # Discrimina por VALOR: lo reportado a dos cédulas distintas es plata de dos personas
    # distintas y no se puede sumar bajo una sola partida.
    reportado_a: str | None
    nota: str | None
    codigos: list[str] = field(default_factory=list)
    celdas: list[str] = field(default_factory=list)
    monto: int = 0
    # None mientras ninguna fila afirme una retención: el XLSX real no trae esa columna.
    retencion: int | None = None

    @property
    def id(self) -> str:
        """`nit:concepto` en el caso normal (contrato del plan); las variantes que el
        contrato no contempla extienden el id en vez de colisionar con él."""
        base = f"{self.ref}:{self.clave}"
        if self.reportado_a is not None:
            return f"{base}:reportado-a:{self.reportado_a}"
        return base


def abrir(exogena: DocumentReading) -> list[Partida]:
    """Fase 1: convierte la exógena en partidas. Todas nacen sin lado documento.

    Varias filas de la misma llave se suman (un tercero reporta el mismo concepto en varias
    filas), y cada grupo nace SOLO_DIAN — o CONCEPTO_DESCONOCIDO si su código no mapea.
    """
    titular = _nit(exogena.field("id_number"))
    grupos: dict[tuple[str, str, str | None], _Grupo] = {}
    for posicion, fila in enumerate(exogena.rows):
        valores = fila.values
        nit = _nit(valores.get("reporter_nit"))
        nombre = str(valores.get("reporter_name") or "").strip()
        ref = _ref_tercero(nit, nombre, fila.source, posicion)
        codigo = str(valores.get("concept_code") or "").strip()
        concepto, nota_clasificacion = _concepto_de_fila(valores, codigo)

        # Una fila que el tercero no le reportó al titular es un hecho DISTINTO, no una
        # discrepancia de montos: se agrupa aparte para no contaminar la suma de lo que sí
        # es del titular, y nunca aportará hecho.
        reportado_a, nota_reporte = _reportado_a(titular, valores)
        nota = "; ".join(n for n in (nota_reporte, nota_clasificacion) if n) or None

        # Para un código sin mapear, la identidad del grupo es el código crudo, y a falta
        # de código el texto del concepto: fusionar dos conceptos desconocidos distintos
        # sería asumir que son el mismo hecho (aportes a salud + consignaciones bancarias
        # del mismo tercero no son una partida de 44,5 millones).
        texto = str(valores.get("concept") or "").strip()
        clave = str(concepto) if concepto is not None else (codigo or texto or "desconocido")
        grupo = grupos.setdefault(
            (ref, clave, reportado_a),
            _Grupo(
                ref=ref,
                nit=nit,
                nombre=nombre,
                clave=clave,
                concepto=concepto,
                reportado_a=reportado_a,
                nota=nota,
            ),
        )
        grupo.monto += _entero(valores.get("amount"))
        if "retencion" in valores:
            # Solo cuenta como reportada si la fila trae la clave: el XLSX real no tiene
            # columna de retención, y "no reportada" no puede convertirse en un 0 que
            # después discrepe contra el certificado.
            grupo.retencion = (grupo.retencion or 0) + _entero(valores.get("retencion"))
        if codigo and codigo not in grupo.codigos:
            grupo.codigos.append(codigo)
        if fila.source:
            grupo.celdas.append(fila.source)
    return [_partida_dian(grupo) for grupo in grupos.values()]


def _concepto_de_fila(
    valores: dict[str, object], codigo: str
) -> tuple[Concepto | None, str | None]:
    """El concepto de una fila: primero lo que la DIAN ya resolvió en ella, después el código.

    La columna "Uso declaración Sugerida" dice a qué renglón del 210 va cada valor, y ese
    veredicto manda sobre la tabla de códigos: los reportes reales usan códigos de ingreso
    (5004) también para retenciones, así que una fila que la DIAN manda al renglón de
    retenciones no puede nacer como concepto de ingreso tenga el código que tenga — sería
    un ingreso fantasma, y el crédito de la retención se perdería. Una fila que apunte a
    retenciones Y a otro renglón a la vez es genuinamente ambigua: no se clasifica a la
    ligera, queda pendiente con nota.
    """
    renglones = _renglones(valores)
    if _RENGLON_RETENCIONES in renglones:
        if renglones == {_RENGLON_RETENCIONES}:
            return Concepto.RETENCION, None
        return None, _NOTA_RETENCION_AMBIGUA
    return concepto_de_codigo(codigo), None


def _renglones(valores: dict[str, object]) -> set[int]:
    """Renglones del 210 que la DIAN asignó a la fila.

    Los deja resueltos el lector de exógena (`form_lines`); si la fila viene de una lectura
    que no los trae (vieja o construida a mano), se sacan del texto de la columna "Uso
    declaración Sugerida" con la misma regla que usa el lector.
    """
    en_fila = valores.get("form_lines")
    if isinstance(en_fila, list) and en_fila:
        return {int(n) for n in en_fila}
    return {int(n) for n in _RENGLON_RE.findall(str(valores.get("suggested_use") or ""))}


def _entero(valor: object) -> int:
    """Los montos de una lectura ya vienen como int y pasan tal cual; cualquier otra cosa
    (un float o un texto que se coló) cierra por `dinero.pesos`, el ÚNICO punto de redondeo
    del sistema — un `int()` acá truncaría en vez de redondear."""
    if valor is None or valor == "":
        return 0
    if isinstance(valor, int):
        return valor
    return pesos(valor)


def _nit(valor: object) -> str:
    """Normaliza un NIT o cédula a sus dígitos, sin puntuación ni dígito de verificación.

    La exógena lo trae como texto libre del XLSX ("900.111.222-9", o "900111222.0" cuando
    openpyxl entrega la celda como número), mientras el del 220 llega validado y limpio:
    sin normalizar los dos lados, el mismo empleador eran dos partidas y la plata se
    contaba doble. El DV (un solo dígito tras el guion) no es parte del NIT y se descarta
    ANTES de la limpieza genérica, que lo habría pegado al final — peor que dejarlo.
    """
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor or "").strip()
    if re.fullmatch(r"\d+\.0+", texto):
        texto = texto.split(".", 1)[0]
    if "-" in texto:
        principal, dv = texto.rsplit("-", 1)
        if principal.strip() and len(dv.strip()) == 1 and dv.strip().isdigit():
            texto = principal
    return re.sub(r"[^0-9A-Za-z]", "", texto)


def _ref_tercero(nit: str, nombre: str, fuente: str | None, posicion: int) -> str:
    """Identidad del tercero para la llave y el id, con el NIT como caso normal.

    Sin NIT se cae al nombre, y sin nombre a la fila de origen: dos terceros distintos
    jamás pueden caer en la misma partida. Es el espejo del cuidado que ya tiene el
    camino sin NIT de `incorporar` (que usa el sha del documento).
    """
    if nit:
        return nit
    if nombre:
        return f"nombre:{nombre}"
    return f"fila:{fuente or posicion}"


def _reportado_a(titular: str, valores: dict[str, object]) -> tuple[str | None, str | None]:
    """A quién le reportó el tercero la fila si no fue al titular, y la nota que lo explica.

    El lector de exógena ya decidió esto fila por fila (`reported_to_titular`, que además
    cubre el caso "misma cédula, otro nombre" comparando los nombres normalizados) y su
    docstring pide explícitamente no volver a decidirlo. Se le hace caso; la comparación de
    números queda solo como respaldo para filas que no traigan la conclusión (lecturas
    hechas antes de que existiera o construidas a mano). Sin titular legible no se puede
    afirmar que una fila sea ajena, así que no se marca ninguna.
    """
    reportado_id = _nit(valores.get("reported_id_number"))
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
        id=grupo.id,
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
    claves = TIPO_A_CLAVE.get(documento.doc_type)
    if claves is None:
        # Error de programación del llamador, no un mensaje para el cliente: los tipos que
        # el conciliador sabe cruzar están en TIPO_A_CLAVE y se agregan a conciencia.
        raise ValueError(
            f"El conciliador no sabe cruzar documentos de tipo {documento.doc_type!r}."
        )

    resultado = list(partidas)
    for clave in claves:
        version = _version_documento(documento, clave)
        if version is None:
            # El documento no trae ninguno de los campos de este hecho: no lo afirma.
            continue
        if clave.omitir_en_cero and version.monto == 0:
            continue
        resultado = _incorporar_clave(resultado, documento, clave, version, tolerancia_pesos)
    return resultado


def _incorporar_clave(
    partidas: list[Partida],
    documento: DocumentReading,
    clave: _ClaveDocumento,
    version: Valor,
    tolerancia_pesos: int,
) -> list[Partida]:
    """Cruza UN hecho del documento contra las partidas."""
    nit = _nit(documento.field(clave.campo_nit))
    nombre = str(documento.field(clave.campo_nombre) or "").strip()
    # El identificador corto del documento, el mismo con que el expediente lo refiere. Es
    # la llave del aporte en `versiones_documento`: reincorporar el MISMO documento
    # reemplaza su aporte (idempotente) y un documento NUEVO del mismo pagador suma.
    sha = documento.content_sha256[:12]

    if not nit:
        # Sin NIT no hay llave de tercero (entrada manual o lectura incompleta): la partida
        # es del documento mismo, con el sha como identidad. Reincorporarlo empareja por
        # ese id — este era justo el camino que duplicaba la plata en cada subida.
        id_suelta = f"{sha}:{clave.concepto}"
        indice = next((i for i, p in enumerate(partidas) if p.id == id_suelta), None)
        if indice is not None:
            actualizadas = list(partidas)
            actualizadas[indice] = _emparejar(partidas[indice], sha, version, tolerancia_pesos)
            return actualizadas
        suelta = Partida(
            id=id_suelta,
            nit_tercero="",
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            versiones_documento={sha: version},
            estado=EstadoPartida.SOLO_DOCUMENTO,
            nota=_NOTA_SIN_NIT,
        )
        return [*partidas, suelta]

    objetivo = _indice_emparejable(partidas, nit, clave.concepto)
    if objetivo is None:
        # La DIAN no conoce este hecho (todavía): el documento es la única versión.
        nueva = Partida(
            id=f"{nit}:{clave.concepto}",
            nit_tercero=nit,
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            versiones_documento={sha: version},
            estado=EstadoPartida.SOLO_DOCUMENTO,
        )
        return [*partidas, nueva]

    actualizadas = list(partidas)
    actualizadas[objetivo] = _emparejar(partidas[objetivo], sha, version, tolerancia_pesos)
    return actualizadas


def _version_documento(documento: DocumentReading, clave: _ClaveDocumento) -> Valor | None:
    """Lo que el documento afirma para esta clave, o None si no trae sus campos."""
    campos: dict[str, ExtractedField] = {}
    for campo in documento.fields:
        # Primero gana, igual que `DocumentReading.field()`: con un nombre repetido, un
        # dict comprehension se queda con el ÚLTIMO y el monto saldría de un campo
        # distinto del que cualquier otro consumidor de la lectura ve.
        campos.setdefault(campo.name, campo)
    presentes = [campos[n] for n in clave.campos_monto if n in campos]
    if not presentes:
        return None
    retencion = campos.get(clave.campo_retencion) if clave.campo_retencion else None
    usados = presentes + ([retencion] if retencion is not None else [])
    celdas = [c.source for c in usados if c.source]
    confianzas = [c.confidence for c in usados]
    return Valor(
        monto=sum(_entero(c.value) for c in presentes),
        # None cuando la lectura no trae el campo: este lado no afirma ninguna retención.
        retencion=_entero(retencion.value) if retencion is not None else None,
        lado=Lado.DOCUMENTO,
        # La procedencia viene de la lectura (`ExtractedField.source`/`.confidence`). La
        # confianza del agregado es la mínima de los campos usados: la suma no es más
        # confiable que su peor sumando.
        celda=", ".join(dict.fromkeys(celdas)) or None,
        confianza=min(confianzas) if confianzas else None,
    )


def _indice_emparejable(partidas: list[Partida], nit: str, concepto: Concepto) -> int | None:
    """Contra qué partida cruza el documento, prefiriendo la reportada al titular.

    La del titular se encuentra por su id exacto (`nit:concepto`). Una gemela ajena del
    mismo tercero y concepto tiene OTRO id (lleva a quién se lo reportaron), así que se
    busca por estructura, y solo se toma si es la única: el certificado queda a la vista
    en vez de perderse en silencio.
    """
    id_titular = f"{nit}:{concepto}"
    ajena: int | None = None
    for i, partida in enumerate(partidas):
        if partida.id == id_titular:
            return i
        if (
            ajena is None
            and _es_ajena(partida)
            and partida.nit_tercero == nit
            and partida.concepto is concepto
        ):
            ajena = i
    return ajena


def _es_ajena(partida: Partida) -> bool:
    """La marca es el campo estructural, nunca el texto de la nota: `refrescar` de T5
    reescribe `nota` por spec, y una marca que viviera ahí desaparecería con él."""
    return partida.reportado_a is not None


def _emparejar(partida: Partida, sha: str, version: Valor, tolerancia_pesos: int) -> Partida:
    # Mismo sha = el mismo documento otra vez: su aporte se reemplaza (reenvío corregido).
    # Sha nuevo = otro documento del mismo pagador: se agrega. Nada desaparece en silencio.
    versiones = dict(partida.versiones_documento)
    versiones[sha] = version
    agregado = _agregado(versiones)
    adjuntos: dict[str, object] = {
        "versiones_documento": versiones,
        "version_documento": agregado,
    }

    if _es_ajena(partida):
        # La fila de la DIAN es de otra persona y NUNCA aporta hecho, así que tampoco puede
        # confirmar este certificado: la partida no cambia de estado. El documento queda
        # adjunto y anotado para que el contador vea las dos cosas y decida (puede resolver
        # con otro valor); descartarlo sería una pérdida silenciosa.
        nota = partida.nota or ""
        if _NOTA_CERTIFICADO_SIN_RESPALDO not in nota:
            prefijo = f"{nota}; " if nota else ""
            nota = prefijo + _NOTA_CERTIFICADO_SIN_RESPALDO
        return partida.model_copy(update={**adjuntos, "nota": nota})

    dian = partida.version_dian
    if dian is None:
        # Emparejó contra una partida que ya era solo-documento: sigue sin haber contra
        # qué comparar.
        return partida.model_copy(update={**adjuntos, "estado": EstadoPartida.SOLO_DOCUMENTO})

    # Se compara contra el AGREGADO de los documentos: la exógena también agrega (un banco
    # reporta la suma de sus CDT en una sola fila).
    coincide = abs(dian.monto - agregado.monto) <= tolerancia_pesos
    if dian.retencion is not None and agregado.retencion is not None:
        # La retención solo se compara cuando los DOS lados la afirman: "no reportada"
        # no es un cero contra el cual discrepar.
        coincide = coincide and abs(dian.retencion - agregado.retencion) <= tolerancia_pesos
    return partida.model_copy(
        update={
            **adjuntos,
            "estado": EstadoPartida.COINCIDE if coincide else EstadoPartida.DISCREPANCIA,
        }
    )


def _agregado(versiones: dict[str, Valor]) -> Valor:
    """La suma de los aportes de los documentos, como un solo `Valor` del lado documento.

    Con un solo documento es su aporte tal cual. Con varios: los montos se suman, la
    retención suma solo los lados que la afirman (None si ninguno), la confianza es la
    mínima (la suma no es más confiable que su peor sumando) y las celdas se ordenan para
    que el agregado no dependa del orden de llegada de los documentos.
    """
    valores = list(versiones.values())
    if len(valores) == 1:
        return valores[0]
    retenciones = [v.retencion for v in valores if v.retencion is not None]
    confianzas = [v.confianza for v in valores if v.confianza is not None]
    celdas = sorted({v.celda for v in valores if v.celda})
    return Valor(
        monto=sum(v.monto for v in valores),
        retencion=sum(retenciones) if retenciones else None,
        lado=Lado.DOCUMENTO,
        celda=", ".join(celdas) or None,
        confianza=min(confianzas) if confianzas else None,
    )
