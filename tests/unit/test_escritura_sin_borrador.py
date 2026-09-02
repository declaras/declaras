"""Escribir el 210 de alguien que no tiene NINGÚN formulario en el portal.

═══ EL CASO QUE FALLABA ERA JUSTO PARA EL QUE SE CONSTRUYÓ LA CREACIÓN ═══

`_borrador_del_anio` busca el borrador editable del año y, si no hay, lo crea: es lo que evita
pedirle al contribuyente que entre al portal a crearlo, o sea el paso manual que este módulo
existe para eliminar.

Pero cuando la persona no tiene NI UN formulario, la DIAN responde 404 con "Documentos no
encontrados" a la consulta del listado, y esa excepción reventaba antes de llegar a la línea
que crea. La creación automática funcionaba solo para quien ya tenía formularios de otros años
y fallaba con el primerizo, que es el caso para el que existe.

Se vio en un expediente real, con el borrador ya cerrado y listo: "Falló un paso de la
escritura en el portal: abrir el borrador del año. La DIAN respondió: Documentos no
encontrados". El proceso moría en el último tramo.
"""

from __future__ import annotations

import httpx

from declaras.adapters.dian.endpoints import DIAN_API
from declaras.adapters.dian.rest.api_client import DianApiClient
from declaras.adapters.dian.rest.client import PortalClient, PortalContext
from declaras.adapters.dian.rest.escritura import escribir_borrador

PORTAL = "https://muisca.dian.gov.co"
ANIO = 2025
URI = "/documentos/renta210v18/v1"

# Lo que responde la DIAN a quien no tiene ningún formulario, verificado contra el portal real.
SIN_DOCUMENTOS = {"codigo": 500, "mensaje": "Documentos no encontrados", "descripcion": "..."}


def _molde() -> dict:
    """El documento que el portal prellena cuando se le pide un borrador nuevo.

    LAS CASILLAS VIENEN EN `null`, igual que el molde real: es exactamente lo que la DIAN
    rechaza al crear si no se rellena antes.
    """
    return {
        "doc": {
            "cab": {"cs_id_4": "2100000000000"},
            "cuerpo": {
                "cs_id_24": None,   # actividad económica
                "cs_id_29": None,   # obligatoria, numérica
                "cs_id_30": None,
                "cs_id_31": None,
                "cs_id_335": None,  # bandera 0/1
                "cs_id_27": None,   # texto: NO se debe tocar
            },
        }
    }


def _contexto(handler) -> PortalContext:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http.cookies.set("DIAN-MUISCA", "cookie", domain="muisca.dian.gov.co")
    return PortalContext(
        portal=PortalClient(http, PORTAL),
        api=DianApiClient(http, portal_url=PORTAL),
    )


async def test_sin_ningun_formulario_el_borrador_se_crea_igual():
    """El 404 al listar significa "no tiene ninguno", no "falló la consulta"."""
    guardado: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        ruta = request.url.path

        # El cliente canjea la cookie de sesión por un token antes de cualquier llamada.
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})

        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])

        # LA CLAVE DE LA PRUEBA: la persona no tiene ningún formulario.
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(404, json=SIN_DOCUMENTOS)

        # El molde del borrador nuevo, que es de donde sale el documento a crear.
        if "formularios/borrador" in ruta:
            return httpx.Response(200, json=_molde())

        # La creación: el portal devuelve el documento con su número.
        if ruta.endswith(f"{URI}/formularios") and request.method == "POST":
            return httpx.Response(200, json=_molde())

        # Leer, escribir y releer el borrador ya creado.
        if "/formularios/" in ruta:
            if request.method == "PUT":
                guardado["cuerpo"] = request.content
                return httpx.Response(200, json={})
            return httpx.Response(200, json=_molde())

        return httpx.Response(404, json=SIN_DOCUMENTOS)

    resultado = await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 72_325_681})

    assert resultado.form_id == "2100000000000"
    assert resultado.escritas == 1, "la casilla tenia que llegar al borrador recien creado"
    assert guardado, "tenia que hacerse el PUT sobre el borrador creado"


async def test_si_el_portal_falla_de_verdad_no_se_confunde_con_no_tener_borrador():
    """Un 500 NO es "no tiene formularios": ahí sí hay que parar en vez de crear un borrador
    encima de un portal que está fallando."""
    import pytest

    from declaras.domain.errors import DianPortalUnavailableError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if request.url.path == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        return httpx.Response(500, json={"mensaje": "error interno"})

    with pytest.raises(DianPortalUnavailableError):
        await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 1})


async def test_un_400_dice_el_paso_y_lo_que_respondio_la_dian():
    """El error que llegó a la pantalla como "La DIAN rechazó la consulta (400)" a secas.

    Dos descartes lo dejaron mudo: el motivo del cuerpo solo se leía para el 404, y el
    envoltorio que nombra el paso solo cubría un tipo de error. Un 400 real al crear el
    borrador de un contribuyente pasó por los dos huecos y llegó sin paso y sin porqué:
    imposible de diagnosticar sin reproducirlo a mano.
    """
    import pytest

    from declaras.domain.errors import DianError

    def handler(request: httpx.Request) -> httpx.Response:
        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(404, json=SIN_DOCUMENTOS)
        # El 400 del caso real: la DIAN rechaza crear el borrador y dice por qué en el cuerpo.
        return httpx.Response(
            400, json={"codigo": 400, "mensaje": "El contribuyente no tiene RUT activo"}
        )

    with pytest.raises(DianError) as arrojado:
        await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 1})

    error = arrojado.value
    assert "abrir el borrador del año" in error.message, "tiene que decir el paso"
    assert error.details.get("motivo") == "El contribuyente no tiene RUT activo", (
        "y lo que respondió la DIAN, que es lo único que permite actuar"
    )



def test_el_molde_en_blanco_se_rellena_antes_de_crear():
    """LA CAUSA REAL DEL 400, con las validaciones que la DIAN devolvió palabra por palabra.

    Un molde recién pedido trae las casillas en `null`, y crear un borrador con eso da un 400
    que enumera cada obligatoria vacía: "Casilla Obligatoria" en la 29/30/31, "El valor debe
    ser 1 o 0" en la 335, la actividad económica en la 24. La app de la DIAN rellena esos
    campos antes de su POST; esto hace lo mismo.
    """
    from declaras.adapters.dian.rest.escritura import _preparar_molde_para_crear

    molde = _molde()
    _preparar_molde_para_crear(molde)
    cuerpo = molde["doc"]["cuerpo"]

    # Las numéricas obligatorias, en 0 (no null).
    assert cuerpo["cs_id_29"] == "0"
    assert cuerpo["cs_id_30"] == "0"
    assert cuerpo["cs_id_31"] == "0"
    assert cuerpo["cs_id_335"] == "0"
    # La actividad económica, la genérica.
    assert cuerpo["cs_id_24"] == "0010"
    # El texto NO se toca: un 0 ahí sería el error opuesto.
    assert cuerpo["cs_id_27"] is None


def test_lo_que_el_molde_ya_traia_no_se_pisa():
    """Rellenar es para los vacíos. Un valor que vino se respeta: sobrescribirlo borraría un
    dato que la DIAN sí precargó."""
    from declaras.adapters.dian.rest.escritura import _preparar_molde_para_crear

    molde = {"doc": {"cuerpo": {"cs_id_24": "4321", "cs_id_29": "5000000"}}}
    _preparar_molde_para_crear(molde)

    assert molde["doc"]["cuerpo"]["cs_id_24"] == "4321"
    assert molde["doc"]["cuerpo"]["cs_id_29"] == "5000000"


async def test_no_se_escriben_las_casillas_calculadas():
    """La causa del segundo 400: "Inconsistencia en el Cálculo :: valor sugerido".

    Las casillas `editable: false` las calcula el portal a partir de las de entrada. Escribir
    la 40 (deducciones) o la 91 (total cédula general) es mandarle un valor que no coincide con
    lo que el portal deriva, y lo rechaza. Un humano no las puede tocar —salen en gris—; llena
    las blancas y el sistema calcula el resto.

    Se escribe la 29 (patrimonio bruto, de entrada) y se OMITE la 40 y la 91 (calculadas).
    """
    puesto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        # Ya tiene un borrador editable, para ir directo a escribir sin crear.
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO,
                "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                import json as _json
                puesto.update(_json.loads(request.content)["doc"]["cuerpo"])
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"doc": {"cuerpo": {
                "cs_id_29": None, "cs_id_40": None, "cs_id_91": None,
            }}})
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    await escribir_borrador(
        _contexto(handler), anio=ANIO, casillas={29: 72_000_000, 40: 5_000_000, 91: 60_000_000}
    )

    assert puesto["cs_id_29"] == 72_000_000, "la de entrada sí se escribe"
    # Las calculadas NO llevan nuestro valor: quedan en 0 (limpias), el portal las deriva.
    assert puesto["cs_id_40"] in (None, 0, "0"), "la 40 la calcula el portal, no se escribe"
    assert puesto["cs_id_91"] in (None, 0, "0"), "la 91 (total) tampoco"



async def test_cuando_la_dian_corrige_un_total_se_reenvia_con_su_valor():
    """LA CAUSA REAL DEL SEGUNDO 400 y su solución.

    El portal recalcula las casillas derivadas con su propio motor y rechaza el formulario si
    no coinciden al peso ("Inconsistencia en el Cálculo :: valor sugerido :: N"). En vez de
    reproducir ese motor —frágil—, se le pregunta: se manda lo que tenemos, y si corrige, se
    aplican los valores que él mismo sugirió y se reenvía.
    """
    intentos: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO, "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                cuerpo = _json.loads(request.content)["doc"]["cuerpo"]
                intentos.append(dict(cuerpo))
                # El PRIMER PUT lo rechaza y sugiere que la 91 (total) debe ser 40 millones.
                if len(intentos) == 1:
                    msg = "Inconsistencia en el Cálculo :: valor sugerido :: 40000000"
                    return httpx.Response(400, json={"marcas": [{"idCasilla": "91", "msg": msg}]})
                return httpx.Response(200, json={})  # el segundo, ya corregido, pasa
            return httpx.Response(
                200, json={"doc": {"cuerpo": {"cs_id_29": None, "cs_id_91": None}}}
            )
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 72_000_000})

    assert len(intentos) == 2, "se reenvía una vez con el valor que sugirió la DIAN"
    # El segundo envío ya lleva la 91 con el valor que el portal pidió.
    assert str(intentos[1]["cs_id_91"]) == "40000000"


def test_las_cifras_se_redondean_al_millar():
    """La DIAN rechaza casillas que no sean múltiplo de mil ("Valor no aproximado al múltiplo
    de mil"). El 210 se declara en miles, y no es truncar: es redondeo comercial."""
    from declaras.adapters.dian.rest.escritura import _al_millar

    assert _al_millar(72_325_681) == 72_326_000  # resto 681 >= 500 → sube
    assert _al_millar(72_325_400) == 72_325_000  # resto 400 < 500 → baja
    assert _al_millar(1_500) == 2_000            # el punto medio sube
    assert _al_millar(1_499) == 1_000
    assert _al_millar(0) == 0


async def test_el_valor_escrito_llega_redondeado_al_portal():
    """No solo la función: el número que sale hacia el portal ya viene al millar."""
    puesto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO, "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                puesto.update(_json.loads(request.content)["doc"]["cuerpo"])
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"doc": {"cuerpo": {"cs_id_29": None}}})
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 72_325_681})
    assert puesto["cs_id_29"] == 72_326_000, "el valor exacto se redondeó al millar antes de ir"


async def test_itera_cuando_corregir_una_casilla_desencadena_otra():
    """Corregir una casilla puede cambiar el cálculo de otra (renta líquida → impuesto →
    saldo), así que el portal puede pedir un segundo ajuste tras el primero. El guardado itera
    hasta converger, no una sola vez."""
    intentos: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO, "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                cuerpo = _json.loads(request.content)["doc"]["cuerpo"]
                intentos.append(dict(cuerpo))
                # 1er PUT: pide corregir la 90. 2do: al corregirla, ahora pide la 126. 3ro: pasa.
                sug = "Inconsistencia en el Cálculo :: valor sugerido :: "
                if len(intentos) == 1:
                    return httpx.Response(400, json={"marcas": [
                        {"idCasilla": "90", "type": "error", "msg": sug + "5000000"}]})
                if len(intentos) == 2:
                    return httpx.Response(400, json={"marcas": [
                        {"idCasilla": "126", "type": "error", "msg": sug + "800000"}]})
                return httpx.Response(200, json={})
            vacio = {"cs_id_29": None, "cs_id_90": None, "cs_id_126": None}
            return httpx.Response(200, json={"doc": {"cuerpo": vacio}})
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 72_000_000})

    assert len(intentos) == 3, "iteró hasta que el portal aceptó"
    assert str(intentos[2]["cs_id_90"]) == "5000000"
    assert str(intentos[2]["cs_id_126"]) == "800000"


async def test_las_calculadas_se_limpian_a_cero_del_borrador_reusado():
    """Un borrador reusado arrastra el valor de intentos anteriores en las calculadas. Esa
    basura también se valida, así que se pone en 0 antes de escribir."""
    puesto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO, "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                puesto.update(_json.loads(request.content)["doc"]["cuerpo"])
                return httpx.Response(200, json={})
            # El borrador viene SUCIO: la 90 (calculada) trae basura de un intento anterior.
            return httpx.Response(
                200, json={"doc": {"cuerpo": {"cs_id_29": None, "cs_id_90": "9999999"}}}
            )
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    await escribir_borrador(_contexto(handler), anio=ANIO, casillas={29: 72_000_000})
    assert puesto["cs_id_90"] == "0", "la basura de la calculada se limpió a 0"


async def test_el_numero_de_dependientes_se_escribe_sin_redondear():
    """La casilla 138 es un CONTEO, no pesos: el portal la exige cuando hay deducción por
    dependientes ("Hay un dependiente económico"). Redondearla al millar mandaría 1 → 0."""
    puesto: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        ruta = request.url.path
        if ruta == DIAN_API.token_from_cookies:
            return httpx.Response(200, json={"idToken": "jwt"})
        if ruta == DIAN_API.renta_form_versions:
            return httpx.Response(200, json=[{"anioGravable": ANIO, "uriApi": URI, "version": 18}])
        if ruta.endswith(f"{URI}/formularios") and request.method == "GET":
            return httpx.Response(200, json={"infoFormularios": [{
                "anio": ANIO, "identificador": {"id": "2118"},
                "atributos": {"docAtributos": {"esEditable": True, "esPresentado": False}},
            }]})
        if "/formularios/" in ruta:
            if request.method == "PUT":
                puesto.update(_json.loads(request.content)["doc"]["cuerpo"])
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"doc": {"cuerpo": {"cs_id_138": None}}})
        return httpx.Response(404, json=SIN_DOCUMENTOS)

    # Dos dependientes: la 138 llega en 2, NO redondeada a 0.
    await escribir_borrador(_contexto(handler), anio=ANIO, casillas={138: 2})
    assert str(puesto["cs_id_138"]) == "2", "el conteo se escribe tal cual, no al millar"
