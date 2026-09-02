"""Escribir el 210 en el portal: llenar el borrador del contribuyente y verificarlo.

Es el ultimo tramo del producto —lo unico que hasta hoy hacia el contador a mano era
transcribir las casillas calculadas al Muisca— y el UNICO flujo que modifica algo en la
cuenta del contribuyente. Todo lo demas del conector descarga.

═══ COMO SE DESCUBRIO EL CAMINO (2026-08-19, sin escribir en ninguna cuenta) ═══

El contenido del formulario no vive en `renta210ingreso` (esa API solo lista): vive en una
API versionada por formato cuya ruta publica la propia DIAN en `uriApi` de las versiones.
Los verbos los declaro el recurso en la cabecera `Allow` de un OPTIONS: `GET, POST` sobre
la coleccion y `PUT, GET` sobre el documento. La forma es
`{"doc": {"cab": {...}, "cuerpo": {...}, "pie": {...}}}` con cada casilla como
`cs_id_{numero}`, el MISMO numero que imprime el formulario oficial.

═══ LAS TRES REGLAS DE ESTE MODULO ═══

1. SE ESCRIBE SOBRE EL BORRADOR QUE HAY, no se crea. El POST de creacion existe pero su
   cuerpo no esta calibrado, y un borrador de mas es basura visible en la cuenta de un
   contribuyente. Si no hay borrador editable del año, se dice como crearlo (un clic en el
   portal) en vez de intentarlo a ciegas.

2. CAB Y PIE NO SE TOCAN. La cabecera es la identidad (cedula, nombres) y el pie es control
   y firma. Lo unico nuestro son las casillas del cuerpo que el motor calcula.

3. DESPUES DEL PUT SE RELEE TODO Y SE COMPARA. Un 201 no prueba que lo guardado sea lo
   enviado: en el primer ensayo real el portal respondio 201 y habia corrompido una letra
   por la codificacion. La relectura es la unica prueba, y su resultado viaja en el modelo
   para que el contador lo vea en vez de confiarnos.

═══ LO QUE QUEDA ABIERTO: LAS CASILLAS DERIVADAS ═══

El borrador de la DIAN trae llenas casillas que Clara no calcula (41, 42, 91, 92...):
totales que el formulario web deriva de las de entrada. Al escribir las nuestras y dejar
esas, el documento guardado queda mezclado hasta que alguien lo abra en el portal — lo
esperable es que el formulario web las recalcule al abrirlo, pero NO esta comprobado. Por
eso las "ajenas" viajan en el resultado y la pantalla pide revisarlas antes de firmar: el
que firma tiene que abrir el borrador en el portal de todas formas, y ahi el propio Muisca
rehace sus cuentas.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.adapters.dian.rest.client import PortalContext
from declaras.domain.errors import (
    DianDocumentUnavailableError,
    DianError,
    DianLayoutChangedError,
)
from declaras.domain.models import BorradorEscrito, DiferenciaDeEscritura
from declaras.observability import get_logger

log = get_logger(__name__)

# Casillas del cuerpo que traen texto de control y no cifras: no se reportan como "ajenas"
# aunque tengan valor. La 24 es la actividad economica; la 25-27 son marcas del formulario.
_NO_SON_CIFRAS = frozenset({24, 25, 26, 27})

# La ultima casilla del formulario oficial impreso. El documento del portal guarda ademas
# casillas internas con numeros altos (265, 337, 355...) que son estado derivado del propio
# Muisca: reportarselas al contador como "cifras que Clara no calcula" es ruido que ningun
# humano puede accionar. Medido en el primer ensayo real: 30 "ajenas" de las que 13 eran
# internas.
_ULTIMA_CASILLA_OFICIAL = 139


async def _paso(que_se_hacia: str, tarea: Awaitable[Any]) -> Any:
    """Corre una llamada al portal y, si falla, dice CUAL paso era.

    ═══ CUBRE TODA FALLA DE LA DIAN, NO UN SOLO TIPO ═══

    La primera version solo envolvia el 404 ("documento no disponible"), que era el error del
    dia. Al dia siguiente el mismo flujo fallo con un 400 y llego a la pantalla como "La DIAN
    rechazó la consulta (400)" a secas: sin paso, porque el envoltorio dejaba pasar de largo
    todo lo que no fuera el tipo del dia anterior. Envolver por tipo es jugar a adivinar cual
    sera el proximo error; se envuelve por FAMILIA, que para el portal es una sola.

    El tipo del error SE CONSERVA (el constructor es uniforme: mensaje + detalles), asi que
    quien lo trate por codigo sigue funcionando; solo cambian el mensaje, que ahora nombra el
    paso, y los detalles, que lo cargan.
    """
    try:
        return await tarea
    except DianError as exc:
        # El paso va como frase propia y no interpolado en la del portal: pegados, el punto
        # final quedaba dentro de una oración que arrancaba con el mensaje de la DIAN.
        raise type(exc)(
            f"Falló un paso de la escritura en el portal: {que_se_hacia}. {exc.message}",
            **{**exc.details, "paso": que_se_hacia},
        ) from exc


async def _uri_del_anio(ctx: PortalContext, anio: int) -> str:
    """La ruta de la API del formato para ese año gravable, publicada por la propia DIAN.

    Se resuelve en cada escritura y no se fija en el codigo: el año gravable 2025 va por
    `renta210v18` y el proximo ira por la version que la DIAN publique. Fijarla seria
    escribir con el formato viejo el dia que cambie.
    """
    versiones = await ctx.api.get_json(DIAN_API.renta_form_versions)
    del_anio = [v for v in versiones if v.get("anioGravable") == anio and v.get("uriApi")]
    if not del_anio:
        raise DianLayoutChangedError(
            f"La DIAN no publica una versión del formulario 210 para el año {anio}.",
            doc_type="FORM_210_WRITE",
        )
    # Puede venir mas de una version del mismo año: rige la mas alta.
    mejor = max(del_anio, key=lambda v: v.get("version", 0))
    return "/" + str(mejor["uriApi"]).strip("/")


def _es_editable(info: Mapping[str, Any]) -> bool:
    atributos = (info.get("atributos") or {}).get("docAtributos") or {}
    return bool(atributos.get("esEditable")) and not atributos.get("esPresentado")


# Las casillas NUMERICAS del 210, extraidas de la tabla `keys` del propio bundle de la DIAN
# (`transformarDatosModeloParaEnviar`). Son las que, vacias, la DIAN rechaza como "Casilla
# Obligatoria" al crear: hay que mandarlas en 0. Las que NO estan aca son texto y se dejan como
# vengan. La lista se copia entera y no se resume en rangos porque tiene huecos reales (140 no
# esta, 242 es string) y un rango inventado meteria ceros donde va texto.
_CASILLAS_NUMERICAS = frozenset({
    28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50,
    51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73,
    74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96,
    97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115,
    116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133,
    134, 135, 136, 137, 138, 139, 141, 241, 244, 245, 246, 247, 248, 249, 250, 251, 252, 253,
    254, 255, 256, 265, 266, 267, 270, 271, 272, 273, 274, 275, 276, 277, 278, 279, 280, 281,
    282, 287, 289, 290, 292, 293, 295, 296, 297, 298, 299, 300, 301, 302, 304, 305, 306, 307,
    308, 309, 310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325,
    326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 341, 355, 356, 357, 359,
})

# Actividad economica generica, la que la app pone cuando el contribuyente no trae una
# especifica (`cs_id_24 = '0010'`). Sin ella, la casilla 24 sale como error al crear.
_ACTIVIDAD_ECONOMICA_POR_DEFECTO = "0010"

# Las casillas de SOLO LECTURA del 210 (`editable: false` en la tabla del bundle de la DIAN):
# el portal las CALCULA a partir de las de entrada, y escribirlas es el error "Inconsistencia
# en el Cálculo :: valor sugerido". Un humano en el portal no las puede tocar —salen en gris—;
# llena las blancas (de entrada) y el sistema calcula el resto.
#
# NUESTRO `formulario_210` SI produce muchas de estas, y esta bien: las necesita para MOSTRARLE
# el 210 completo al contador y para comparar contra el borrador de la DIAN. Lo que no se puede
# es MANDARSELAS al portal. Por eso el filtro vive aca, en la escritura, y no en el calculo.
_CASILLAS_CALCULADAS = frozenset({
    27, 31, 34, 37, 40, 41, 42, 46, 49, 52, 53, 54, 55, 57, 61, 65, 68, 69, 70, 71, 73, 78, 82,
    85, 86, 87, 88, 90, 91, 92, 93, 97, 101, 103, 106, 108, 116, 117, 118, 119, 120, 121, 122,
    126, 130, 135, 137, 138, 139, 141, 241, 242, 244, 245, 246, 247, 248, 249, 250, 251, 252,
    253, 254, 255, 256, 265, 266, 267, 270, 271, 272, 273, 274, 275, 276, 277, 278, 279, 280,
    281, 282, 287, 288, 289, 290, 291, 292, 293, 294, 295, 296, 298, 299, 306, 309, 312, 315,
    316, 317, 320, 323, 326, 329, 332, 335, 341, 357, 358, 359,
})


def _preparar_molde_para_crear(molde: Mapping[str, Any]) -> None:
    """Rellena el molde en blanco con lo que la DIAN exige para CREAR, in place.

    Un molde recien pedido trae las casillas en `null`. Crear un borrador con eso da un 400 que
    lista cada casilla obligatoria vacia. La app de la DIAN, antes de su POST, convierte los
    numeros vacios a 0 y pone la actividad economica; esto hace lo mismo, casilla por casilla,
    con la tabla de tipos que la propia app define.

    No se tocan las casillas de texto: meterles un 0 seria el error opuesto, un numero donde va
    una cadena.
    """
    doc = molde.get("doc") if isinstance(molde, dict) else None
    cuerpo = doc.get("cuerpo") if isinstance(doc, dict) else None
    if not isinstance(cuerpo, dict):
        return
    for numero in _CASILLAS_NUMERICAS:
        clave = f"cs_id_{numero}"
        if clave in cuerpo and cuerpo[clave] in (None, ""):
            cuerpo[clave] = "0"
    # La actividad economica: si el molde no la trajo, la generica. Si la trajo, se respeta.
    if not cuerpo.get("cs_id_24"):
        cuerpo["cs_id_24"] = _ACTIVIDAD_ECONOMICA_POR_DEFECTO


async def _guardar_con_calculo_del_portal(
    ctx: PortalContext, ruta: str, documento: dict[str, Any], cuerpo: dict[str, Any]
) -> None:
    """Guarda el borrador dejando que la DIAN calcule sus propias casillas derivadas.

    ═══ POR QUE NO ALCANZA CON MANDAR NUESTROS TOTALES ═══

    El portal no solo guarda: RECALCULA cada casilla derivada (renta liquida, totales, saldos)
    con su motor —el del art. 336 y sus limites— y rechaza el formulario si lo que le mandamos
    no coincide al peso. Nuestro motor liquida bien, pero el mapeo a las ~200 casillas del 210
    es una aproximacion, y una diferencia de redondeo en una casilla intermedia tumba el
    guardado entero con "Inconsistencia en el Calculo :: valor sugerido :: N".

    Reproducir el motor del portal casilla por casilla seria fragil y se rompe cada vez que la
    DIAN cambia una formula. Pero el portal, en ese mismo rechazo, DICE cuanto deberia valer
    cada casilla ("valor sugerido"). Asi que se le pregunta: se manda lo que tenemos, y si
    corrige, se aplican sus valores y se reenvia. Es determinista y usa el motor de la DIAN, no
    una copia nuestra que envejece.

    Un solo reintento: con los valores que el propio portal sugirio, el segundo envio cuadra o
    el problema es otro y hay que verlo, no insistir.
    """
    try:
        await _paso("guardar el borrador", ctx.api.put_json(ruta, documento))
        return
    except DianError as exc:
        sugeridos = {
            int(m["casilla"]): m["sugerido"]
            for m in exc.details.get("marcas", [])
            if isinstance(m, dict) and "sugerido" in m
        }
        if not sugeridos:
            raise  # no hay nada que corregir: es otro error, se propaga con su paso

    log.info("dian.write.aplica_sugeridos", casillas=len(sugeridos))
    for numero, valor in sugeridos.items():
        clave = f"cs_id_{numero}"
        if clave not in cuerpo:
            continue
        existente = cuerpo[clave]
        cuerpo[clave] = str(valor) if isinstance(existente, str) else int(valor)
    await _paso("guardar el borrador (con los valores que sugirió la DIAN)",
                ctx.api.put_json(ruta, documento))


async def _crear_borrador(ctx: PortalContext, uri: str, anio: int) -> str:
    """Crea el borrador del año, igual que el boton "Haga su declaracion de renta" del portal.

    ES UNA COPIA FIEL DE LO QUE HACE LA APP DE LA DIAN, leida de su propio bundle
    (`DFormularioServicio.crearFormulario`): se pide el MOLDE que el portal prellena
    (`GET formularios/borrador?modo=inicial&anio=...`) y se manda de vuelta entero
    (`POST formularios`). El cuerpo no se inventa ni se arma a mano — es el documento que
    la propia DIAN acaba de entregar, con sus 214 casillas y su cabecera.

    Sin esto, el contribuyente tenia que entrar al portal a crear el borrador antes de que
    Clara pudiera llenarlo, y eso rompia la promesa del producto: cerrar la declaracion y
    que lo unico que quede sea entrar a firmar.
    """
    molde = await ctx.api.get_json(
        f"{uri}/formularios/borrador?modo=inicial&anio={anio}&periodicidad=anual&periodo=null"
    )
    # EL MOLDE EN BLANCO NO SE PUEDE MANDAR TAL CUAL, y esto costó descubrirlo: la DIAN lo
    # rechaza con un 400 que enumera casilla por casilla lo que le falta ("Casilla Obligatoria"
    # en la 29, 30, 31...; "El valor debe ser 1 o 0" en la 335 y la 253; la actividad económica
    # en la 24). El molde trae esas casillas en `null`, y crear un borrador exige que las
    # numéricas sean 0 y las banderas 0 o 1. La app de la DIAN hace exactamente esto antes de
    # su POST (leído de `transformarDatosModeloParaEnviar` + `guardarActualizarBorrador` en su
    # bundle): rellena la actividad y convierte los vacíos a cero. Se replica igual.
    _preparar_molde_para_crear(molde)
    creado = await ctx.api.post_json(f"{uri}/formularios", molde)

    # La respuesta del portal anida el documento creado; el id tambien viaja en el mensaje.
    doc = _documento_de(creado)
    nuevo_id = (doc.get("cab") or {}).get("cs_id_4") if doc else None
    if not nuevo_id:
        raise DianLayoutChangedError(
            "La DIAN creó el borrador pero no devolvió su número. Hay que recalibrar la "
            "lectura de esa respuesta antes de volver a escribir.",
            doc_type="FORM_210_WRITE",
        )
    log.info("dian.write.borrador_creado", form_id=str(nuevo_id), anio=anio)
    return str(nuevo_id)


def _documento_de(respuesta: Any) -> dict[str, Any] | None:
    """El `doc` dentro de la respuesta del portal, este donde este.

    La API envuelve distinto segun la operacion (`{"doc": ...}` en el GET, y anidado bajo
    `respuesta.listaResultados.resultado.textoResultado` en el POST), asi que se busca en
    vez de asumir una forma: asumirla es lo que se rompe cuando cambian el envoltorio.
    """
    if isinstance(respuesta, dict):
        doc = respuesta.get("doc")
        if isinstance(doc, dict):
            return dict(doc)
        for valor in respuesta.values():
            encontrado = _documento_de(valor)
            if encontrado is not None:
                return encontrado
    return None


async def _borrador_del_anio(ctx: PortalContext, uri: str, anio: int) -> str:
    """El borrador editable del año, y si no hay ninguno lo crea.

    ═══ UN 404 AL LISTAR ES "NO TIENE NINGUNO", NO UN ERROR ═══

    Y esa distinción era justo la que faltaba. Cuando el contribuyente no tiene NI UN
    formulario, la DIAN responde 404 con "Documentos no encontrados" a la consulta del listado.
    Esa excepción reventaba aquí y nunca se llegaba a la línea que crea el borrador — o sea que
    la creación automática funcionaba solo para quien YA tenía formularios de otros años, y
    fallaba exactamente en el caso para el que se construyó: el primerizo, o el que nunca abrió
    borrador de este año.

    Se vio en un expediente real: "Falló un paso de la escritura en el portal: abrir el
    borrador del año. La DIAN respondió: Documentos no encontrados". Con el borrador cerrado y
    listo, el proceso moría en el último tramo.

    El resto del sistema ya trataba ese 404 así (`api_client` lo documenta: "un 404 aquí casi
    nunca es un error, es la DIAN diciendo que no tiene ese documento"); lo que faltaba era
    consumirlo con ese significado en vez de dejarlo subir.
    """
    try:
        listado = await ctx.api.get_json(f"{uri}/formularios")
    except DianDocumentUnavailableError:
        log.info("dian.write.sin_formularios", anio=anio)
        listado = None
    formularios = (listado or {}).get("infoFormularios") or []
    for info in formularios:
        if info.get("anio") == anio and _es_editable(info):
            return str(info["identificador"]["id"])
    # NO HAY: se crea, que es lo que haria una persona en el portal con un clic. Pedirle al
    # contribuyente que lo haga el mismo era dejar un paso manual justo en el tramo que este
    # modulo existe para eliminar.
    return await _crear_borrador(ctx, uri, anio)


def _mismo_valor(a: Any, b: Any) -> bool:
    """Compara lo enviado con lo releido tolerando el tipo: el portal devuelve numeros
    a veces como int y a veces como str, y `1000 != "1000"` seria una discrepancia falsa."""
    if a == b:
        return True
    return str(a) == str(b)


async def escribir_borrador(
    ctx: PortalContext, *, anio: int, casillas: Mapping[int, int]
) -> BorradorEscrito:
    """Llena el borrador del año con las casillas calculadas y verifica lo que quedo.

    ═══ CADA PASO DICE CUAL ES ═══

    Este flujo hace cinco llamadas al portal (versión del formato, buscar el borrador, leerlo,
    escribirlo, releerlo) y un 404 en cualquiera de ellas salía con el mismo texto genérico:
    "la DIAN no tiene ese documento". Al escribir eso apunta al lado contrario —parece que
    falta un documento del contribuyente cuando lo que falló fue crear su borrador— y deja a
    quien opera sin saber en qué paso se cayó.

    `_paso` envuelve cada llamada y le pone nombre al que falla.
    """
    uri = await _paso("consultar la versión del formulario", _uri_del_anio(ctx, anio))
    form_id = await _paso("abrir el borrador del año", _borrador_del_anio(ctx, uri, anio))
    ruta = f"{uri}/formularios/{form_id}"

    documento = await _paso("leer el borrador", ctx.api.get_json(ruta))
    cuerpo = documento["doc"]["cuerpo"]

    enviadas: dict[int, int] = {}
    for numero, valor in casillas.items():
        clave = f"cs_id_{numero}"
        if numero in _CASILLAS_CALCULADAS:
            # El portal la calcula solo. Mandarla es el error "Inconsistencia en el Cálculo":
            # nuestro valor y el que el portal deriva de las casillas de entrada no coinciden,
            # y no tienen por que —el borrador nuevo aun no tiene las entradas que la alimentan
            # cuando el portal la evalua. Se omite y el portal la llena al recalcular.
            continue
        if clave not in cuerpo:
            # Una casilla que el formato no tiene no se inventa: agregar claves que el
            # documento no trae es pedirle al portal que interprete algo no calibrado.
            log.warning("dian.write.casilla_sin_destino", casilla=numero, anio=anio)
            continue
        # Se respeta el tipo que el documento ya trae: donde hay str se escribe str.
        existente = cuerpo[clave]
        cuerpo[clave] = str(valor) if isinstance(existente, str) else int(valor)
        enviadas[numero] = int(valor)

    log.info("dian.write.put", form_id=form_id, anio=anio, casillas=len(enviadas))
    await _guardar_con_calculo_del_portal(ctx, ruta, documento, cuerpo)

    # ═══ LA RELECTURA ═══
    releido = await _paso("releer el borrador para verificarlo", ctx.api.get_json(ruta))
    cuerpo_releido = releido["doc"]["cuerpo"]

    diferencias = [
        DiferenciaDeEscritura(
            casilla=numero, enviado=valor, leido=cuerpo_releido.get(f"cs_id_{numero}")
        )
        for numero, valor in enviadas.items()
        if not _mismo_valor(valor, cuerpo_releido.get(f"cs_id_{numero}"))
    ]

    ajenas: dict[int, int | str] = {}
    for clave, valor in cuerpo_releido.items():
        if not clave.startswith("cs_id_"):
            continue
        numero = int(clave.removeprefix("cs_id_"))
        if numero in enviadas or numero in _NO_SON_CIFRAS or numero > _ULTIMA_CASILLA_OFICIAL:
            continue
        # Solo lo que tiene valor de verdad: un 0 o un None no le dicen nada a nadie.
        if valor in (None, 0, "0", ""):
            continue
        ajenas[numero] = valor

    resultado = BorradorEscrito(
        form_id=form_id,
        anio=anio,
        escritas=len(enviadas),
        verificado=not diferencias,
        diferencias=diferencias,
        ajenas=ajenas,
    )
    log.info(
        "dian.write.verified",
        form_id=form_id,
        verificado=resultado.verificado,
        diferencias=len(diferencias),
        ajenas=len(ajenas),
    )
    return resultado
