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
from declaras.domain.errors import DianDocumentUnavailableError, DianLayoutChangedError
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
    """Corre una llamada al portal y, si falla por documento ausente, dice CUAL era.

    Un 404 del portal llega siempre como "la DIAN no tiene ese documento", que es correcto
    para una descarga y desorientador al escribir: no falta un documento del contribuyente,
    falló un paso concreto de la escritura. Se conserva el código (quien lo trate por código
    sigue funcionando) y el motivo que dio la DIAN, y se le agrega el paso.
    """
    try:
        return await tarea
    except DianDocumentUnavailableError as exc:
        raise DianDocumentUnavailableError(
            # El paso va como frase propia y no interpolado en la del portal: pegados, el punto
            # final quedaba dentro de una oración que arrancaba con el mensaje de la DIAN.
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
    listado = await ctx.api.get_json(f"{uri}/formularios")
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
    await _paso("guardar el borrador", ctx.api.put_json(ruta, documento))

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
