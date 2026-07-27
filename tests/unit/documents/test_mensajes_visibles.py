"""Los avisos de los lectores los lee una persona, no un desarrollador.

Cada aviso de lectura termina como un pendiente del expediente, y ahi lo puede ver quien va a
firmar la declaracion. Sin una regla que lo impida, la mitad de los avisos quedan escritos como
notas internas ("no se encontro el encabezado de la tabla"): en minuscula, sin tildes y con
nombres de campo del codigo. Paso, y estos casos son lo que evita que vuelva a pasar.
"""

from __future__ import annotations

import re

import pytest

from declaras.documents.parsers import einvoice_summary, exogena, renta_210, rut
from tests.documents_fixtures import (
    build_einvoice_summary_xlsx,
    build_exogena_xlsx,
    build_renta_210_pdf,
    build_rut_pdf,
)

# Un identificador del codigo que se escapo al texto: dos palabras unidas por guion bajo.
_IDENTIFICADOR = re.compile(r"\b[a-z]+_[a-z_]+\b")

# Palabras que en el codigo se escriben sin tilde por convencion (los comentarios y los nombres
# van en ASCII) y que al llegar a un texto de usuario quedan mal escritas. Solo se listan formas
# que no existen en espanol, para no marcar falsos positivos: "invalida" o "esta" son palabras
# validas segun el contexto, "informacion" no lo es en ningun caso.
_SIN_TILDE = re.compile(
    r"\b(anio|codigo|numero|numerico|sesion|informacion|declaracion|identificacion|"
    r"verificacion|operacion|electronica|expiro|solicito|categoria|dia|limite|"
    r"credito|deposito|articulo|parrafo|mas)\b",
    re.IGNORECASE,
)


def _todos_los_avisos():
    """Avisos de los cuatro lectores, forzando los casos que los producen."""
    lecturas = [
        exogena.parse(build_exogena_xlsx(taxpayer_name="PEREZ JOS�")),
        exogena.parse(
            build_exogena_xlsx(
                taxpayer_name="VALENCIA MORENO JUAN JOSE",
                detail_rows=[
                    {
                        "reporter_nit": "900111222",
                        "reporter_name": "ZPN ARQUIREDES SAS",
                        "concept": "Servicios (Concepto: 5004)",
                        "amount": 7_330_000,
                        "suggested_use": "Tope 1: Ingresos brutos",
                        "reported_name": "Alejandra Delgado Bautista",
                    }
                ],
            )
        ),
        rut.parse(build_rut_pdf()),
        einvoice_summary.parse(build_einvoice_summary_xlsx()),
        renta_210.parse(build_renta_210_pdf(patrimonio_liquido=99_000_000)),
        renta_210.parse(build_renta_210_pdf()),
    ]
    return [w for lectura in lecturas for w in lectura.warnings]


@pytest.mark.parametrize("aviso", _todos_los_avisos(), ids=lambda w: w.code)
def test_el_aviso_esta_escrito_para_una_persona(aviso):
    assert aviso.message[0].isupper(), f"empieza en minúscula: {aviso.message!r}"
    assert aviso.message.rstrip().endswith(
        ("."),
    ), f"no termina en punto: {aviso.message!r}"
    filtrado = _IDENTIFICADOR.search(aviso.message)
    assert filtrado is None, f"filtra un nombre del código ({filtrado.group()}): {aviso.message!r}"


def test_hay_avisos_que_probar():
    """Si los fixtures dejaran de producir avisos, los casos de arriba pasarian sin probar nada."""
    codigos = {w.code for w in _todos_los_avisos()}
    assert codigos >= {
        "TEXT_ENCODING_DAMAGED",
        "REPORTED_TO_ANOTHER_PERSON",
        "FORM_ARITHMETIC_MISMATCH",
    }


# ─────── los mensajes de las fallas ───────
#
# El mensaje por defecto de cada falla tambien lo lee una persona: sale en la pantalla cuando
# algo no funciona, que es justo cuando peor cae leer una nota de desarrollador. Estaban todos
# escritos sin tildes, como si fueran comentarios del codigo.


def _todas_las_fallas():
    """Todas las fallas del sistema, esten donde esten.

    Se recorren los modulos y no solo `domain.errors`, porque hay fallas definidas fuera: la de
    autenticacion vive en la capa de API, y escapaba a esta comprobacion justamente por eso.
    """
    import importlib
    import pkgutil

    import declaras
    from declaras.domain.errors import DeclarasError

    for info in pkgutil.walk_packages(declaras.__path__, f"{declaras.__name__}."):
        importlib.import_module(info.name)

    def descendientes(clase):
        for hija in clase.__subclasses__():
            yield hija
            yield from descendientes(hija)

    return sorted(
        {c for c in descendientes(DeclarasError) if c.default_message},
        key=lambda c: c.__name__,
    )


@pytest.mark.parametrize("falla", _todas_las_fallas(), ids=lambda c: c.__name__)
def test_la_falla_se_explica_en_espanol_correcto(falla):
    _revisar_texto_de_usuario(falla.default_message)


def _revisar_texto_de_usuario(texto: str) -> None:
    """Las tres reglas de un texto que va a leer una persona.

    Los huecos de formato (`{codigo}`) se quitan antes de revisar: son nombres de variables del
    codigo, y lo que importa es como esta escrito el texto alrededor.
    """
    texto = re.sub(r"\{[^}]*\}", "", texto).strip() or texto
    assert texto[0].isupper(), f"empieza en minúscula: {texto!r}"
    assert texto.rstrip().endswith("."), f"no termina en punto: {texto!r}"

    identificador = _IDENTIFICADOR.search(texto)
    assert identificador is None, (
        f"filtra un nombre del código ({identificador.group()}): {texto!r}"
    )

    sin_tilde = _SIN_TILDE.search(texto)
    assert sin_tilde is None, f"le falta la tilde a «{sin_tilde.group()}» en: {texto!r}"


def test_ninguna_falla_habla_de_expedientes_ni_de_flags():
    """Vocabulario interno que no significa nada para quien tiene que declarar."""
    for falla in _todas_las_fallas():
        for palabra in ("expediente", "flag", "job "):
            assert palabra not in falla.default_message.lower(), falla.__name__


# ─────── los mensajes escritos a mano al lanzar una falla ───────
#
# Un mensaje puesto en el sitio donde se lanza la falla reemplaza al de la clase, asi que llega
# igual a la pantalla. Estaban casi todos en minuscula y sin tildes: no bastaba con revisar los
# mensajes por defecto.

# Captura el mensaje completo aunque este partido en varias lineas: Python concatena las cadenas
# contiguas, asi que revisar solo el primer pedazo daria por incompleto un texto que si esta bien.
_MENSAJE_AL_LANZAR = re.compile(r"\b[A-Z]\w*Error\(\s*((?:f?\"[^\"]*\"\s*)+)", re.MULTILINE)
_PEDAZO = re.compile(r'f?"([^"]*)"')

# El conector de navegador quedo superado por el de HTTP y no se ejecuta (ver ADR 0003).
_NO_SE_EJECUTA = ("adapters/dian/flows/", "adapters/dian/browser.py", "adapters/dian/selectors.py")

# El nucleo de calculo (motor, optimizador, parametros, render, caso) y los extractores levantan
# `ValueError` pelado para violar un invariante propio: parametros de otro anio gravable, tabla del
# art. 241 con tramos no contiguos, PDF con dos certificados. Son contratos con quien programa, y
# los fijan los 139 casos que trajo el motor (6 de ellos goldens de punta a punta); pedirles
# ademas prosa de usuario seria pedirles algo que casi nadie lee. Se los saca de este recorrido.
#
# "Casi": hay TRES caminos por los que un texto crudo llega al usuario, y solo el primero esta
# cerrado. Quien vaya a angostar esta exclusion tiene que mirar los otros dos, no confiar en que
# el nucleo es invisible.
#
#   1. HTTP generico (`api/errors.py`, manejador de `Exception`). CERRADO: responde con
#      `DeclarasError()` pelado y no repite el texto de la excepcion. Verificado.
#   2. La alerta del expediente (`services/case_service.py`, ramas `except DocumentUnreadableError`
#      y `except DocumentReaderUnavailableError`, que escriben `exc.message` dentro de un flag que
#      lee el contador). Es el camino por el que el nucleo entraba al registrarse `leer_220` como
#      lector del 220: `case_service` es el unico que llama a un lector, y por eso
#      `documents/parsers/certificados.py` traduce ahi las fallas del extractor con mensajes
#      propios, que SI revisa la comprobacion de abajo porque `documents/` no esta excluido.
#      CERRADO para los lectores.
#      El worker de extracciones (`services/extraction.py`, rama `except Exception`) envuelve el
#      texto crudo de CUALQUIER excepcion (`DeclarasError(str(exc)[:200])`), `mark_failed` lo
#      persiste y `ExtractionResponse.error` lo devuelve por `GET /v1/extractions/{id}`: es el
#      camino que justifica la regex ancha de Juan, pero ese servicio baja y guarda documentos y
#      NO llama a ningun lector, asi que el nucleo de calculo no pasa por ahi.
#   3. `_domain_validation_error` (`api/errors.py`). ABIERTO: hace eco de los mensajes de los
#      validadores de pydantic en un 422. En el nucleo excluido hay SIETE: `caso/modelos.py:44`,
#      alcanzable con datos del cliente, y `parametros/modelos.py:70,72,78,86,92,97`, que solo
#      se disparan con un `ag<anio>.yaml` malo (error de configuracion, no de quien declara).
#
# DEUDA ANOTADA, 7 mensajes: los validadores de pydantic del punto 3. El de `caso/modelos.py`
# espera a la T5/T6, cuando el conciliador exponga el Caso por la API.
#
# Los mensajes de `extraccion/` (el 220 en `f220.py`, y la mecanica compartida —el pre-flight, la
# respuesta sin salida estructurada, el ano gravable— en `_base.py`) ya no son de cara al usuario:
# con la frontera del punto 2 puesta, su unica audiencia son el log y quien programa (es el
# contrato que fijan las 28 pruebas del 220). Se les corrigio igual el punto final y la mayuscula
# que les faltaban, porque terminan en un log que lee una persona, pero se quedan fuera de esta
# comprobacion a proposito, y hoy por DOS razones: uno dice `stop_reason=refusal` y otro nombra el
# campo `anio_gravable` que le falta al esquema, que es exactamente lo que hay que ver al depurar
# y exactamente lo que esta comprobacion prohibe. Angostar la exclusion hasta ahi seria pedirle
# prosa de usuario a un mensaje que ningun usuario ve.
#
# Ademas rendiria poco: la regex de abajo solo ve el mensaje cuando es el PRIMER argumento del
# `raise`, y en `extraccion/` va detras del motivo (`Extraccion220InvalidaError(Motivo220.X, "…")`),
# asi que quitar el prefijo dejaria expuesto un mensaje de los tres, no los tres.
_NUCLEO_DE_CALCULO = ("motor/", "optimizador/", "parametros/", "render/", "caso/", "extraccion/")


def _mensajes_escritos_a_mano():
    from pathlib import Path

    import declaras

    raiz = Path(declaras.__file__).parent
    for archivo in sorted(raiz.rglob("*.py")):
        relativo = str(archivo.relative_to(raiz))
        # `startswith` y no `in`: las dos listas son prefijos de ruta. Con `in`, un futuro
        # `api/render/` quedaria excluido en silencio por el patron "render/".
        if relativo.startswith(_NO_SE_EJECUTA + _NUCLEO_DE_CALCULO):
            continue
        for bloque in _MENSAJE_AL_LANZAR.findall(archivo.read_text()):
            mensaje = "".join(_PEDAZO.findall(bloque))
            # Los mensajes muy cortos son marcadores internos, no frases para leer.
            if len(mensaje) >= 12:
                yield relativo, mensaje


@pytest.mark.parametrize(
    ("archivo", "mensaje"), list(_mensajes_escritos_a_mano()), ids=lambda x: str(x)[:40]
)
def test_el_mensaje_de_la_falla_esta_escrito_para_una_persona(archivo, mensaje):
    _revisar_texto_de_usuario(mensaje)


def test_las_capas_que_le_hablan_al_usuario_siguen_revisadas():
    """Que la lista de exclusiones no se coma la comprobacion sin que nadie lo note.

    Un `_NUCLEO_DE_CALCULO` mal escrito (una ruta de mas, un prefijo que atrapa medio arbol) dejaria
    la prueba de arriba con dos casos y pasando. Estas son las capas que si le hablan a una persona.
    """
    revisadas = {archivo.split("/")[0] for archivo, _ in _mensajes_escritos_a_mano()}
    assert revisadas >= {"adapters", "api", "documents", "services", "domain"}
