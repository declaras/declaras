from enum import StrEnum

from pydantic import BaseModel, Field

from declaras.caso import Fuente, IngresoLaboral
from declaras.extraccion._base import REGLAS_COMUNES, ExtraccionInvalidaError, extraer

# Re-exportado: `id_documento` se importaba desde acá antes de que la mecánica se mudara a la
# base, y a quien calcula la identidad de un PDF no le importa dónde vive la función.
from declaras.extraccion._base import id_documento as id_documento


class Motivo220(StrEnum):
    """Por qué se rechazó una extracción. Son las causas, no los mensajes.

    Existen porque quien traduce la falla para una persona necesita saber CUÁL guard falló:
    las causas piden acciones distintas (subir los certificados de a uno, buscar el del año
    correcto, registrar la pensión aparte) y un consejo que no corresponde a la causa manda a
    pedir de nuevo un archivo que estaba bien. Adivinar leyendo el texto del mensaje sería
    atarse a su redacción.

    Las tres primeras son las de la base (`MotivoExtraccion`) y por eso repiten sus valores: la
    extracción del 220 las reetiqueta con este vocabulario para que la frontera despache su pista
    sin tener que conocer dos enumeraciones.
    """

    NO_ES_PDF = "no_es_pdf"
    SIN_SALIDA = "sin_salida"
    OTRO_ANIO = "otro_anio"
    VARIOS_CERTIFICADOS = "varios_certificados"
    NO_RECONCILIA = "no_reconcilia"
    TIENE_PENSIONES = "tiene_pensiones"


class Extraccion220InvalidaError(ExtraccionInvalidaError[Motivo220]):
    """Falla de un guard del 220, con su motivo etiquetado.

    Sigue siendo `ValueError` —el contrato del extractor, que fijan sus 28 pruebas y del que
    dependen los `match=` de sus mensajes— y además dice cuál guard falló.
    """


# Diferencia máxima tolerada entre la suma de los campos y el total impreso. Cubre el
# redondeo del propio certificado; por encima de esto la extracción no es confiable.
TOLERANCIA_RECONCILIACION_PESOS = 1_000

PROMPT_220 = f"""Este PDF es un Certificado de Ingresos y Retenciones (Formulario 220
de la DIAN, Colombia). Extrae los valores EXACTOS en pesos.

Ubica cada valor por su ETIQUETA impresa, no por número de casilla: la numeración
cambia entre años del formato, las etiquetas no.

- salarios: "Pagos por salarios".
- cesantias_e_intereses: "Cesantías e intereses de cesantías efectivamente pagadas
  o consignadas".
- prima: "Pagos por prestaciones sociales" — el 220 agrupa acá prima, vacaciones y
  demás prestaciones.
- bonificaciones: la SUMA de los demás pagos laborales del certificado (otros pagos,
  viáticos gravados, gastos de representación, comisiones, compensaciones).
- total_ingresos_brutos: el "Total de ingresos brutos" tal como lo imprime el
  certificado. NO lo recalcules: cópialo.
- pensiones_de_jubilacion: pagos por pensiones de jubilación, invalidez o vejez;
  0 si el certificado no las reporta. Las pensiones NUNCA van en bonificaciones ni en
  ningún otro campo: van SOLO acá.
- aportes_salud y aportes_pension: aportes OBLIGATORIOS del trabajador
  (pension incluye fondo de solidaridad si viene sumado).
- retencion: total retención en la fuente practicada en el año.
- empleador_nit: solo dígitos, sin puntos y sin dígito de verificación.
- anio_gravable: el año gravable que declara el certificado.
- numero_de_certificados: cuántos certificados 220 DISTINTOS hay en este PDF
  (normalmente 1; más de uno si trae varios empleadores o varios años).

Reglas que no puedes violar:
- Cada peso del certificado se cuenta EXACTAMENTE una vez entre salarios,
  cesantias_e_intereses, prima, bonificaciones y pensiones_de_jubilacion: ni se
  duplica ni se omite. Las pensiones de jubilación, invalidez o vejez van
  SOLO en pensiones_de_jubilacion, nunca dobladas dentro de bonificaciones.
{REGLAS_COMUNES}"""


class Extraccion220(BaseModel):
    # 7-8 dígitos = NIT de persona natural (cédula), empleador legítimo y masivo.
    empleador_nit: str = Field(pattern=r"^\d{7,10}$")
    empleador_nombre: str
    anio_gravable: int
    numero_de_certificados: int = Field(ge=0)
    total_ingresos_brutos: int = Field(ge=0)
    salarios: int = Field(ge=0)
    cesantias_e_intereses: int = Field(default=0, ge=0)
    prima: int = Field(default=0, ge=0)
    bonificaciones: int = Field(default=0, ge=0)
    # SIN default a propósito: el modelo debe declararla siempre, aunque sea 0. Si fuera
    # opcional, un modelo que la omite deja pasar un 220 mixto como laboral puro.
    pensiones_de_jubilacion: int = Field(ge=0)
    aportes_salud: int = Field(ge=0)
    aportes_pension: int = Field(ge=0)
    retencion: int = Field(default=0, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_220(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client=None,
) -> IngresoLaboral:
    """Extrae un 220 con LLM y devuelve el hecho con proveniencia. Único punto con IA.

    Falla RUIDOSO: esto alimenta un formulario tributario, así que cualquier duda sobre
    la extracción es un `ValueError`, nunca un número silenciosamente equivocado.
    """
    return extraer_220_con_metadatos(pdf_bytes, anio_esperado=anio_esperado, client=client)[0]


def extraer_220_con_metadatos(
    pdf_bytes: bytes,
    anio_esperado: int | None = None,
    client=None,
) -> tuple[IngresoLaboral, Extraccion220]:
    """Lo mismo, y además la extracción cruda que produjo el modelo.

    `IngresoLaboral` es un hecho del caso y solo lleva dinero: el año gravable y el total
    impreso son metadatos de la extracción y no tienen dónde vivir ahí. Quien convierte el
    certificado en una lectura de documento (`documents/parsers/certificados.py`) sí los
    necesita —el año es con lo que después se detecta un certificado que no corresponde al
    caso—, así que se exponen por acá en vez de meterlos en el modelo del caso.
    """
    try:
        ext, doc_id = extraer(
            pdf_bytes,
            schema=Extraccion220,
            prompt=PROMPT_220,
            # El año NO se le delega a la base, que lo verifica al recibir la respuesta: el
            # guard de abajo tiene que ir antes, porque con dos certificados en el PDF no se
            # sabe de cuál es el `anio_gravable` que se estaría comparando.
            anio_esperado=None,
            client=client,
        )
    except ExtraccionInvalidaError as exc:
        # Las causas de la base salen con el vocabulario del 220 para que la frontera
        # (`documents/parsers/certificados.py`) despache la pista que corresponde a la causa.
        # OJO con el orden: `pydantic.ValidationError` también es `ValueError`, así que atrapar
        # `ValueError` acá se comería la validación del esquema y la reetiquetaría con un motivo
        # que no es. No es `ExtraccionInvalidaError` y sigue de largo, como hasta ahora.
        raise Extraccion220InvalidaError(Motivo220(exc.motivo), str(exc)) from exc

    if ext.numero_de_certificados != 1:
        # Con dos certificados en el PDF no se sabe de cuál salió cada cifra.
        raise Extraccion220InvalidaError(
            Motivo220.VARIOS_CERTIFICADOS,
            f"El PDF contiene {ext.numero_de_certificados} certificados; procesa uno a la vez.",
        )

    # Identidad del documento primero: el error más común es subir el 220 del año
    # equivocado, y este chequeo no puede falso-rechazar.
    if anio_esperado is not None and ext.anio_gravable != anio_esperado:
        raise Extraccion220InvalidaError(
            Motivo220.OTRO_ANIO,
            f"El certificado es del año gravable {ext.anio_gravable} "
            f"y se esperaba {anio_esperado}.",
        )

    # Las pensiones entran en la suma porque el "Total de ingresos brutos" impreso las
    # incluye. Va ANTES del guard de pensiones a propósito: así el término es
    # load-bearing (un 220 mixto bien extraído reconcilia gracias a él) y por tanto
    # verificable, en vez de ser código correcto pero inalcanzable.
    suma = (
        ext.salarios
        + ext.cesantias_e_intereses
        + ext.prima
        + ext.bonificaciones
        + ext.pensiones_de_jubilacion
    )
    if abs(suma - ext.total_ingresos_brutos) > TOLERANCIA_RECONCILIACION_PESOS:
        # El total impreso es el testigo independiente: si los campos no lo reproducen,
        # el LLM se saltó una casilla o contó una dos veces. Si el total descuadra
        # tampoco se puede confiar en el campo de pensiones, así que este mensaje gana.
        raise Extraccion220InvalidaError(
            Motivo220.NO_RECONCILIA,
            "La extracción no reconcilia contra el total impreso del certificado: "
            f"los campos suman {suma:,} y el certificado dice "
            f"{ext.total_ingresos_brutos:,}.",
        )

    if ext.pensiones_de_jubilacion > 0:
        # La pensión se exime POR MES (IngresoPension.mesadas), no anual: registrarla
        # como laboral cambia el impuesto.
        raise Extraccion220InvalidaError(
            Motivo220.TIENE_PENSIONES,
            f"El 220 reporta pensiones ({ext.pensiones_de_jubilacion:,}); "
            "regístralas como IngresoPension, no laboral.",
        )

    laboral = IngresoLaboral(
        empleador_nit=ext.empleador_nit,
        empleador_nombre=ext.empleador_nombre,
        salarios=ext.salarios,
        cesantias_e_intereses=ext.cesantias_e_intereses,
        prima=ext.prima,
        bonificaciones=ext.bonificaciones,
        aportes_salud=ext.aportes_salud,
        aportes_pension=ext.aportes_pension,
        retencion=ext.retencion,
        fuente=Fuente.documento("220", doc_id, confianza=ext.confianza),
    )
    return laboral, ext
