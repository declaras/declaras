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
    "reportado al número del titular pero a nombre de otra persona; hay que confirmar si es suyo"
)

_NOTA_SIN_NIT = "el documento no trae el NIT del tercero, así que no se pudo cruzar con la exógena"
_NOTA_RETENCION_AMBIGUA = (
    "la DIAN asigna esta fila a retenciones y a otro renglón a la vez; hay que clasificarla a mano"
)
_NOTA_AJENAS = (
    "el tercero tiene filas reportadas a otras personas; "
    "el certificado queda aparte para cruzarlo a mano"
)

# R132 del formulario 210: "Retenciones año gravable a declarar". Es el renglón con que la
# columna "Uso declaración Sugerida" marca las filas que son retención practicada, no ingreso.
_RENGLON_RETENCIONES = 132
# Los renglones patrimoniales del 210: lo que la persona TIENE al 31 de diciembre, no lo que se
# gano en el año. R29 patrimonio bruto, R30 deudas, R31 patrimonio liquido (que es la resta).
_RENGLON_PATRIMONIO = 29
_RENGLON_DEUDAS = 30
_RENGLONES_PATRIMONIALES = frozenset({29, 30, 31})
# El saldo a favor arrastrado del anio anterior. Va a un renglon del 210, asi que no cae en
# "no va a ninguno", pero tampoco es ingreso ni patrimonio.
_RENGLON_SALDO_FAVOR_ANTERIOR = 131
# Los renglones de INCRNGO del 210, uno por cédula: R33 rentas de trabajo, R44 honorarios,
# R59 rentas de capital, R76 no laborales, R100 pensiones. Una fila que la DIAN manda a uno de
# estos es un ingreso NO CONSTITUTIVO de renta: RESTA de la base, no suma.
#
# Los aportes obligatorios de salud y pensión llegan marcados con varios de ellos a la vez
# (el de pensión con [33, 59, 76]), porque el mismo aporte se imputa a la cédula donde esté el
# ingreso que lo generó. Con cualquiera basta para saber que la fila no es ingreso.
_RENGLONES_INCRNGO = frozenset({33, 44, 59, 76, 100})
# R36 y sus equivalentes por cédula: "otras rentas exentas". La DIAN manda ahí las cesantías y
# el promedio salarial del semestre, que es el insumo de su exención.
_RENGLONES_EXENTAS = frozenset({36, 48, 64, 81})
# R32: "Ingresos brutos (rentas de trabajo)". Es donde la DIAN manda los pagos de nómina.
_RENGLON_INGRESO_TRABAJO = 32

# Cómo se distingue, DENTRO del formato 2276, un aporte de salud de uno de pensión y el dato del
# promedio salarial. El renglón dice que la fila es INCRNGO pero no cuál de los dos aportes es, y
# esa diferencia importa: el motor los suma juntos como INCRNGO pero el 220 los certifica por
# separado, así que tienen que emparejar con la partida correcta.
#
# Se busca en el TEXTO del concepto porque es lo único que los separa. Los patrones son de la
# redacción oficial de la DIAN, verificada contra un reporte real:
#   "Aportes obligatorios a salud a cargo Trabajador"
#   "Aporte obligatorio fondos pensiones y solidaridad a cargo del trabajador"
#   "Valor ingreso laboral promedio de los últimos seis meses"
#   "Cesantías e intereses de cesantías pagadas al empleado"
#   "Cesantías consignadas al fondo de cesantías"
_TEXTO_A_CONCEPTO: tuple[tuple[re.Pattern[str], Concepto], ...] = (
    (re.compile(r"aporte.*salud", re.I), Concepto.APORTES_SALUD),
    (re.compile(r"aporte.*pensi", re.I), Concepto.APORTES_PENSION),
    (re.compile(r"ingreso laboral promedio", re.I), Concepto.PROMEDIO_SALARIAL),
    (re.compile(r"cesant", re.I), Concepto.CESANTIAS),
)
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
    # ¿Documentos DISTINTOS del mismo tercero se suman? True solo cuando el tipo emite
    # varios por tercero de verdad (un banco: un certificado por CDT). False para el 220:
    # el sha es identidad de BYTES, no de documento — el mismo certificado re-escaneado o
    # re-exportado llega con otro hash (el repo lo documenta para las descargas del
    # portal), y sumarlo duplicaría salarios y aportes en silencio. En ese caso rige la
    # última versión, con nota, y el contador decide. OJO: la agregación solo aplica con
    # NIT — presupone saber que los documentos son del mismo tercero, que es justo lo que
    # el camino sin NIT no sabe; ahí siempre manda el ruling de F1 (rivales anotados).
    acumulable: bool = False


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
    # Registrar el lector no alcanza: sin clave, el certificado entra al expediente, se queda
    # en la bandeja y su petición no se cierra nunca. Cada tipo con lector va con la suya.
    "CERT_PENSION": (
        _ClaveDocumento(
            concepto=Concepto.PENSIONES,
            campo_nit="pagador_nit",
            campo_nombre="pagador_nombre",
            # El total anual, que es lo que la exógena reporta agregado por pagador. Las doce
            # mesadas son el detalle del que depende la exención mensual y no tienen
            # contraparte en ninguna fila de la exógena, así que no cruzan: viajan en la
            # lectura para que el caso las use.
            campos_monto=("total_pagado",),
            campo_retencion="retencion",
        ),
    ),
    "CERT_BANCARIO": (
        _ClaveDocumento(
            concepto=Concepto.RENDIMIENTOS,
            campo_nit="entidad_nit",
            campo_nombre="entidad_nombre",
            campos_monto=("rendimientos",),
            campo_retencion="retencion",
            # Acumulable de verdad: un banco emite un certificado por CDT o por producto, y
            # dos certificados distintos del mismo banco son dos rendimientos, no el mismo
            # dos veces. Es el caso para el que existe la bandera.
            acumulable=True,
        ),
    ),
    "CERT_DIVIDENDOS": (
        _ClaveDocumento(
            concepto=Concepto.DIVIDENDOS,
            campo_nit="sociedad_nit",
            campo_nombre="sociedad_nombre",
            # Las dos bolsas suman el total distribuido, que es lo que la exógena reporta.
            # La separación gravado/no gravado no tiene contraparte allá y viaja en la lectura.
            campos_monto=("gravados", "no_gravados"),
            campo_retencion="retencion",
            # NO acumulable: una sociedad certifica el año una vez, y el sha es identidad de
            # BYTES, así que el mismo certificado re-escaneado sumaría dividendos en silencio.
            # Si de verdad hubo dos distribuciones con dos certificados, el segundo queda como
            # rival anotado y lo decide el contador: subdeclarar es malo, pero subdeclarar
            # avisando es preferible a duplicar callado.
        ),
    ),
    "CERT_ARRIENDO": (
        _ClaveDocumento(
            concepto=Concepto.ARRENDAMIENTOS,
            campo_nit="contraparte_nit",
            campo_nombre="contraparte_nombre",
            campos_monto=("canon_total",),
            campo_retencion="retencion",
            # Acumulable: un propietario con dos inmuebles administrados por la misma
            # inmobiliaria recibe dos certificados de la misma contraparte, y son dos
            # arriendos. La exógena los reporta agregados bajo el NIT de la inmobiliaria.
            acumulable=True,
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
        contrato no contempla extienden el id en vez de colisionar con él.

        ESTABILIDAD, para quien persista resoluciones (T5): el id extendido es estable al
        re-parsear el MISMO archivo, pero hay tres condiciones —medidas, no teóricas— en
        que cambia entre consultas: (1) un tercero sin NIT ni nombre cae a la fila de
        origen (`fila:...`), y si la DIAN republica el reporte con una fila insertada más
        arriba, el id se corre; (2) un tercero sin NIT cae a su nombre (`nombre:...`,
        `_ref_tercero`), que es texto libre del XLSX: `nombre:ACME S.A.S.` y
        `nombre:ACME SAS` son dos ids para el mismo tercero si lo escriben distinto en la
        siguiente republicación; (3) una ajena por nombre lleva el nombre en el sufijo
        (`:reportado-a:...`), con la misma fragilidad. En todos los casos una `Resolucion`
        anclada a `partida.id` vía `Fuente.conciliacion` queda huérfana EN SILENCIO. Es
        una decisión de diseño asumida: el `refrescar` de T5 debe tratar la resolución
        cuyo id ya no existe como pendiente de nuevo, no como resuelta.
        """
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

        # Una fila que no va a ninguna casilla del 210 no abre partida. Su única función es
        # determinar si la persona está obligada a declarar, y eso ya lo calculan los cinco
        # topes, que se muestran aparte. Abrirla la ponía en la cola como una decisión que no
        # existe: no hay ninguna respuesta posible a "qué hacemos con los movimientos de tu
        # cuenta de ahorros", porque no se declaran en ningún renglón.
        if concepto in (Concepto.SOLO_PARA_TOPE, Concepto.SALDO_FAVOR_ANTERIOR):
            continue

        # Una fila que el tercero no le reportó al titular es un hecho DISTINTO, no una
        # discrepancia de montos: se agrupa aparte para no contaminar la suma de lo que sí
        # es del titular, y nunca aportará hecho.
        reportado_a, nota_reporte = _reportado_a(titular, valores)
        nota = "; ".join(n for n in (nota_reporte, nota_clasificacion) if n) or None

        # Para un código sin mapear, la identidad del grupo es el código crudo, y a falta
        # de código el texto del concepto: fusionar dos conceptos desconocidos distintos
        # sería asumir que son el mismo hecho (aportes a salud + consignaciones bancarias
        # del mismo tercero no son una partida de 44,5 millones). El prefijo separa los
        # espacios de nombres: sin él, una fila sin código cuyo texto fuera exactamente
        # "SALARIOS" caería en la misma partida que el 5001.
        texto = str(valores.get("concept") or "").strip()
        if concepto is not None:
            clave = str(concepto)
        elif codigo:
            clave = f"codigo:{codigo}"
        elif texto:
            clave = f"texto:{texto}"
        else:
            clave = "desconocido"
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
        # UN PROMEDIO NO SE SUMA, y es el único concepto de la exógena que no es una cantidad de
        # plata acumulable. "Valor ingreso laboral promedio de los últimos seis meses" es una
        # magnitud por vínculo: dos filas del mismo tercero (dos vínculos en el año, una
        # corrección del reporte) sumadas dan un número que no significa nada. Medido: dos filas
        # de $3.500.000 y de 400 UVT daban un promedio de $23.419.600, o sea 470 UVT, un tramo de
        # exención que ninguna de las dos afirma.
        #
        # Rige la MAYOR, que es la que menos exención concede (art. 206 num. 4 baja el porcentaje
        # exento a medida que sube el promedio). Quedarse con la menor sería elegir la fuente que
        # más conviene sobre un hecho que nadie concilió.
        if concepto is Concepto.PROMEDIO_SALARIAL:
            grupo.monto = max(grupo.monto, _entero(valores.get("amount")))
        else:
            grupo.monto += _entero(valores.get("amount"))
        if valores.get("retencion") is not None:
            # Solo cuenta como reportada si la fila trae un VALOR: el XLSX real no tiene
            # columna de retención, y el lector emite None para celdas ausentes por
            # convención — ni la clave ausente ni None pueden convertirse en un 0 que
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

    del_codigo = concepto_de_codigo(codigo)
    if del_codigo is not None:
        return del_codigo, None

    # EL TEXTO, SOLO DESPUÉS DEL CÓDIGO. Identifica los conceptos que ningún código distingue (los
    # siete del formato 2276), y va detrás a propósito: consultado ANTES pisaba códigos que sí están
    # bien mapeados. Medido cuando lo puse primero: "Activos aportes parafiscales, salud, pensión y
    # cesantías" (código 2214, que es PATRIMONIO) caía en APORTES_SALUD por decir "salud", y
    # "Activos laborales reales consolidados trabajador sin cesantías" (2215) caía en CESANTIAS por
    # decir "cesantías". Los dos son saldos al 31 de diciembre: convertirlos en aportes del año
    # habría inventado una deducción de $6.430.250.
    del_texto = _concepto_del_texto(valores)
    if del_texto is not None:
        return del_texto, None

    # La DIAN dice que la fila es INCRNGO. Es un ingreso no constitutivo de renta, así que resta de
    # la base en vez de sumar; sin clasificarlo, entra como ingreso y la base sale inflada dos veces
    # (por lo que suma y por lo que deja de restar).
    #
    # Cae acá el aporte que el texto no nombró; los que sí (salud o pensión) ya salieron arriba con
    # su concepto propio, porque el 220 los certifica por separado y tienen que emparejar.
    if renglones & _RENGLONES_INCRNGO:
        return Concepto.APORTES_SALUD, None

    # R32 es "Ingresos brutos (rentas de trabajo)", y ahí manda la DIAN los pagos de nómina que
    # reporta en el formato 2276: "Pagos por salarios" y "Pagos por prestaciones sociales",
    # verificados los dos en un reporte real. Sin esta regla quedaban CONCEPTO_DESCONOCIDO al sacar
    # el 2276 del mapeo por código, y el salario entero desaparecía del caso.
    #
    # Se exige que R32 sea el ÚNICO renglón de la fila: una que toque R32 y algo más es otra cosa
    # (las cesantías consignadas van a R29 y R36) y no se clasifica a la ligera.
    if renglones == {_RENGLON_INGRESO_TRABAJO}:
        return Concepto.SALARIOS, None

    # Sin código mapeado, el veredicto de la DIAN alcanza para clasificar por NATURALEZA, que es
    # lo que decide si la fila es una decisión o no. Tres clases, y solo la primera es trabajo:
    #
    #   va a un renglón de ingreso    hay que decidir qué cifra rige y si es del titular
    #   va solo a R29/R30             es un saldo al 31 de diciembre: se declara, no se decide
    #   no va a ningún renglón        solo determina si está obligado a declarar
    #
    # Sin esto las tres abrían partida por igual: en un caso real, 26 renglones en la cola del
    # contador de los cuales 18 no se declaran en ninguna casilla del 210.
    if not renglones:
        return Concepto.SOLO_PARA_TOPE, None
    if renglones == {_RENGLON_SALDO_FAVOR_ANTERIOR}:
        return Concepto.SALDO_FAVOR_ANTERIOR, None
    if renglones <= _RENGLONES_PATRIMONIALES:
        # Una fila que toca los dos (el reporte de saldos bancarios manda a R29 y R30 a la vez)
        # es un activo: el saldo de una cuenta suma al patrimonio, y R30 aparece porque el mismo
        # formato de reporte cubre los sobregiros.
        es_solo_deuda = renglones == {_RENGLON_DEUDAS}
        return (Concepto.DEUDA if es_solo_deuda else Concepto.PATRIMONIO), None
    return None, None


def _concepto_del_texto(valores: dict[str, object]) -> Concepto | None:
    """El concepto que el TEXTO de la fila identifica, cuando el código no alcanza.

    Solo se consulta para los conceptos que ningún código distingue (los del formato 2276). No es
    una heurística general sobre el texto: es una tabla de patrones de la redacción oficial de la
    DIAN, y lo que no coincide sigue el camino normal del código y del renglón.
    """
    texto = str(valores.get("concept") or "")
    if not texto:
        return None
    for patron, concepto in _TEXTO_A_CONCEPTO:
        if patron.search(texto):
            return concepto
    return None


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
        EstadoPartida.CONCEPTO_DESCONOCIDO if grupo.concepto is None else EstadoPartida.SOLO_DIAN
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
            tercero=grupo.nombre or None,
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
    # la llave del aporte en `versiones_documento`: reincorporar el MISMO documento es
    # no-op (los bytes ya vistos no pueden mover la cifra declarada) y solo un documento
    # NUEVO aporta versión.
    sha = documento.content_sha256[:12]

    if not nit:
        # Sin NIT no hay llave de tercero (entrada manual o lectura incompleta), y tampoco
        # se puede distinguir en principio "dos escaneos del mismo certificado" de "dos
        # certificados de dos terceros que no pudimos identificar" — así que no se adivina
        # (ruling de la ronda 4): la identidad de la partida es (doc_type, concepto),
        # estable entre subidas, y varios documentos que caigan en ella son RIVALES que
        # decide una persona, nunca una suma. Con el sha en el id, dos escaneos del mismo
        # 220 eran dos partidas que nunca se encontraban y duplicaban la plata en silencio.
        id_suelta = f"sin-nit:{documento.doc_type}:{clave.concepto}"
        indice = next((i for i, p in enumerate(partidas) if p.id == id_suelta), None)
        if indice is not None:
            actualizadas = list(partidas)
            actualizadas[indice] = _emparejar(
                partidas[indice],
                sha,
                version,
                tolerancia_pesos,
                clave.acumulable,
                sin_nit=True,
            )
            return actualizadas
        suelta = Partida(
            id=id_suelta,
            nit_tercero="",
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            versiones_documento={sha: version},
            version_que_rige=sha,
            estado=EstadoPartida.SOLO_DOCUMENTO,
            nota=_NOTA_SIN_NIT,
        )
        return [*partidas, suelta]

    objetivo, gemelas_ajenas = _indice_emparejable(partidas, nit, clave.concepto)
    if objetivo is None:
        # La DIAN no conoce este hecho (todavía), o solo tiene filas ajenas: el documento
        # es la única versión del titular y nace como su propia partida.
        nueva = Partida(
            id=f"{nit}:{clave.concepto}",
            nit_tercero=nit,
            nombre_tercero=nombre,
            concepto=clave.concepto,
            version_documento=version,
            versiones_documento={sha: version},
            version_que_rige=sha,
            estado=EstadoPartida.SOLO_DOCUMENTO,
            nota=_NOTA_AJENAS if gemelas_ajenas else None,
        )
        actualizadas = [*partidas, nueva]
        emparejada = nueva
    else:
        actualizadas = list(partidas)
        emparejada = _emparejar(
            partidas[objetivo], sha, version, tolerancia_pesos, clave.acumulable
        )
        actualizadas[objetivo] = emparejada
    if emparejada.version_dian is None:
        # El hecho del documento quedó sin respaldo DIAN del titular: cada gemela ajena
        # guarda la marca de que este documento podría corresponderle.
        _marcar_ajenas(actualizadas, gemelas_ajenas, sha)
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
        # El nombre del tercero según ESTE documento: sin NIT es el único dato con que el
        # contador distingue "mismo certificado repetido" de "dos terceros distintos".
        tercero=str(documento.field(clave.campo_nombre) or "").strip() or None,
        # La procedencia viene de la lectura (`ExtractedField.source`/`.confidence`). La
        # confianza del agregado es la mínima de los campos usados: la suma no es más
        # confiable que su peor sumando.
        celda=", ".join(dict.fromkeys(celdas)) or None,
        confianza=min(confianzas) if confianzas else None,
    )


def _indice_emparejable(
    partidas: list[Partida], nit: str, concepto: Concepto
) -> tuple[int | None, list[int]]:
    """Contra qué partida cruza el documento, y qué gemelas ajenas hay del mismo hecho.

    El documento solo empareja contra la partida del TITULAR (id exacto `nit:concepto`,
    tenga lado DIAN o venga de un documento anterior). Una gemela ajena del mismo tercero
    y concepto —otro id: lleva a quién se lo reportaron— NUNCA lo recibe, ni siquiera
    siendo única: el certificado es evidencia sobre el titular y no puede vivir dentro de
    una partida que dice explícitamente "esto no es del titular" y cuyas diferencias van
    forzadas a 0 — `pendientes` de T5 la ordenaría de última y la tabla de decisiones no
    permite USAR_DOCUMENTO sobre SOLO_DIAN: el certificado quedaba estacionado donde
    nadie lo ve, mientras los aportes del mismo 220 sí abrían partida propia (un
    `IngresoLaboral` con 0 de salario y los aportes completos).

    INTERPRETACIÓN AUTORIZADA (ronda 4), que desvía del test del brief que exigía UNA
    sola partida cuando el certificado llega contra exactamente una fila ajena: el
    certificado SIEMPRE abre su propia partida SOLO_DOCUMENTO y las ajenas conservan en
    `documentos_por_cruzar` la marca estructural de que llegó. Un agente futuro no debe
    volver al emparejamiento con la ajena "única" — es el mismo trato que
    `Valor.retencion: int | None`.
    """
    id_titular = f"{nit}:{concepto}"
    indice = next((i for i, p in enumerate(partidas) if p.id == id_titular), None)
    ajenas = [
        i
        for i, p in enumerate(partidas)
        if _es_ajena(p) and p.nit_tercero == nit and p.concepto is concepto
    ]
    return indice, ajenas


def _marcar_ajenas(partidas: list[Partida], indices: list[int], sha: str) -> None:
    """Deja en cada gemela ajena la marca de que llegó un documento que podría
    corresponderle. Es estructural (`documentos_por_cruzar`), no una nota: el texto libre
    lo reescribe `refrescar` de T5 por spec, y la marca desaparecería con él (I5)."""
    for i in indices:
        partida = partidas[i]
        if sha not in partida.documentos_por_cruzar:
            partidas[i] = partida.model_copy(
                update={"documentos_por_cruzar": [*partida.documentos_por_cruzar, sha]}
            )


def _es_ajena(partida: Partida) -> bool:
    """La marca es el campo estructural, nunca el texto de la nota: `refrescar` de T5
    reescribe `nota` por spec, y una marca que viviera ahí desaparecería con él."""
    return partida.reportado_a is not None


def _emparejar(
    partida: Partida,
    sha: str,
    version: Valor,
    tolerancia_pesos: int,
    acumulable: bool,
    *,
    sin_nit: bool = False,
) -> Partida:
    if sha in partida.versiones_documento:
        # Los mismos bytes otra vez (retry del job de ingesta, el cliente re-sube el
        # primer archivo): NO-OP. Tomar 'el último PROCESADO' como versión que rige
        # cambiaba la cifra declarada sin documento nuevo (COINCIDE→DISCREPANCIA y al
        # revés). "Nuevo vs. reenvío" es MEMBRESÍA en `versiones_documento` — no el orden
        # de las claves, que JSONB no preserva al persistir (cierre de T4).
        return partida
    # Sha nuevo: se guarda SIEMPRE en `versiones_documento` — nada desaparece —, pero lo
    # que se publica depende del tipo de documento.
    versiones = dict(partida.versiones_documento)
    versiones[sha] = version
    # La rivalidad es de CIFRAS, no de bytes: el mismo PDF re-exportado (cifras iguales,
    # sha distinto) no le da nada que decidir al contador y no ensucia su cola. La
    # excepción es el camino sin NIT: ahí hasta cifras iguales podrían ser dos terceros
    # distintos, así que cualquier segunda versión se anota.
    rivales = len(versiones) > 1 and (sin_nit or _cifras_difieren(versiones))
    if acumulable and not sin_nit:
        # Documentos distintos que sí se suman (un certificado por CDT): el agregado, que
        # es lo que la exógena también reporta. Sin NIT nunca: la agregación presupone lo
        # único que ese camino no tiene —saber que los documentos son del MISMO tercero—
        # y sumar dos escaneos del mismo certificado bancario duplicaría la plata (F1
        # literal por esta rama, que se evaluaba antes que la de rivales).
        publicada = _agregado(versiones)
        nota_rivales = None
        # Con varios documentos lo publicado es el agregado y ningún sha único lo
        # respalda: el respaldo es el conjunto completo de `versiones_documento`.
        version_que_rige = sha if len(versiones) == 1 else None
    elif rivales:
        # Tipo NO acumulable con versiones en disputa: el sha distingue bytes, no
        # documentos, así que esto es casi siempre el mismo certificado re-escaneado.
        # Sumar duplicaría la plata en silencio; rige la última versión NUEVA en llegar y
        # una persona decide. Cuál rigió queda ESTRUCTURAL en la partida (la nota es texto
        # libre que otras capas reescriben): la huella de auditoría de la cifra publicada.
        publicada = version
        nota_rivales = _nota_rivales(len(versiones), sin_nit=sin_nit)
        version_que_rige = sha
    else:
        # Una sola versión, o varias con las MISMAS cifras: la publicada es la más
        # reciente y no hay rivalidad que anotar — pero el respaldo SÍ queda identificado.
        # Antes solo se fijaba con rivales, y la procedencia publicada (celda, confianza)
        # apuntaba a un documento imposible de identificar después de persistir: JSONB no
        # conserva el orden de claves de un objeto (cierre de T4).
        publicada = version
        nota_rivales = None
        version_que_rige = sha
    adjuntos: dict[str, object] = {
        "versiones_documento": versiones,
        "version_documento": publicada,
        "version_que_rige": version_que_rige,
    }
    if sin_nit and publicada.tercero:
        # La partida sin NIT se presenta con el nombre de la versión publicada: mostrar el
        # nombre del primer documento junto a la cifra del último ("ACME — 30M" cuando el
        # certificado de ACME dice 50M) le quitaba al contador el único dato con que
        # decide si los rivales son el mismo certificado o dos terceros distintos.
        adjuntos["nombre_tercero"] = publicada.tercero
    # El aviso de rivales anterior se quita antes de sumar el vigente: el número cambió.
    # (Una partida ajena nunca llega acá: `_indice_emparejable` no la devuelve como
    # objetivo — el certificado abre su propia partida y la ajena guarda la marca.)
    nota_base = _sin_nota_rivales(partida.nota)

    dian = partida.version_dian
    if dian is None:
        # Emparejó contra una partida que ya era solo-documento: sigue sin haber contra
        # qué comparar.
        return partida.model_copy(
            update={
                **adjuntos,
                "estado": EstadoPartida.SOLO_DOCUMENTO,
                "nota": _con_nota(nota_base, nota_rivales),
            }
        )

    # Se compara contra lo publicado: el agregado si el tipo acumula, o la versión que rige.
    coincide = abs(dian.monto - publicada.monto) <= tolerancia_pesos
    if dian.retencion is not None and publicada.retencion is not None:
        # La retención solo se compara cuando los DOS lados la afirman: "no reportada"
        # no es un cero contra el cual discrepar.
        coincide = coincide and abs(dian.retencion - publicada.retencion) <= tolerancia_pesos
    return partida.model_copy(
        update={
            **adjuntos,
            "estado": EstadoPartida.COINCIDE if coincide else EstadoPartida.DISCREPANCIA,
            "nota": _con_nota(nota_base, nota_rivales),
        }
    )


def _con_nota(nota: str | None, *nuevas: str | None) -> str | None:
    """Suma notas sin repetirlas ni pisar las que ya había."""
    for nueva in nuevas:
        if nueva and nueva not in (nota or ""):
            nota = f"{nota}; {nueva}" if nota else nueva
    return nota


def _cifras_difieren(versiones: dict[str, Valor]) -> bool:
    """¿Hay más de una cifra en juego entre las versiones? Se comparan los DOS números
    que la partida declara (monto y retención; None cuenta distinto de 0: 'no la reportó'
    no es 'reportó cero'). La procedencia (celda, confianza) no hace rivalidad."""
    return len({(v.monto, v.retencion) for v in versiones.values()}) > 1


def _nota_rivales(n: int, *, sin_nit: bool) -> str:
    """El aviso de versiones rivales, con el número REAL de versiones en juego.

    Sin NIT el aviso dice la ambigüedad completa: no hay forma de saber si son versiones
    del mismo certificado o certificados de terceros distintos, y esa es exactamente la
    decisión que se le pide al contador.
    """
    if sin_nit:
        return (
            f"llegaron {n} documentos sin NIT del mismo tipo; pueden ser el mismo "
            "certificado repetido o terceros distintos: hay que cruzarlos a mano"
        )
    return f"llegaron {n} certificados distintos del mismo empleador; hay que decidir cuál rige"


_NOTA_RIVALES_RE = re.compile(
    r"(?:; )?llegaron \d+ (?:"
    r"certificados distintos del mismo empleador; hay que decidir cuál rige"
    r"|documentos sin NIT del mismo tipo; pueden ser el mismo certificado repetido "
    r"o terceros distintos: hay que cruzarlos a mano"
    r")"
)


def _sin_nota_rivales(nota: str | None) -> str | None:
    """Quita el aviso de rivales anterior: el número de versiones pudo cambiar, y dos
    avisos con números distintos conviviendo en la misma nota confunden más que ninguno."""
    if not nota:
        return nota
    limpia = _NOTA_RIVALES_RE.sub("", nota).strip("; ")
    return limpia or None


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
    terceros = {v.tercero for v in valores if v.tercero}
    return Valor(
        monto=sum(v.monto for v in valores),
        retencion=sum(retenciones) if retenciones else None,
        lado=Lado.DOCUMENTO,
        # El nombre solo se afirma si TODOS los sumandos lo afirman igual.
        tercero=next(iter(terceros)) if len(terceros) == 1 else None,
        celda=", ".join(celdas) or None,
        confianza=min(confianzas) if confianzas else None,
    )
