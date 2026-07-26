import base64
import hashlib

from pydantic import BaseModel, Field

from declaras.caso import Fuente, IngresoLaboral

MODELO = "claude-opus-5"

# Diferencia máxima tolerada entre la suma de los campos y el total impreso. Cubre el
# redondeo del propio certificado; por encima de esto la extracción no es confiable.
TOLERANCIA_RECONCILIACION_PESOS = 1_000

PROMPT_220 = """Este PDF es un Certificado de Ingresos y Retenciones (Formulario 220
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
1. Cada peso del certificado se cuenta EXACTAMENTE una vez entre salarios,
   cesantias_e_intereses, prima, bonificaciones y pensiones_de_jubilacion: ni se
   duplica ni se omite. Las pensiones de jubilación, invalidez o vejez van
   SOLO en pensiones_de_jubilacion, nunca dobladas dentro de bonificaciones.
2. Los valores van en pesos completos, sin puntos ni separadores. Si el certificado
   indica "cifras en miles", multiplica por 1.000.
3. confianza: tu confianza global 0.0-1.0 en la extracción (baja si el PDF es
   escaneado borroso o el formato es atípico)."""


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
    if not pdf_bytes.startswith(b"%PDF"):
        # Pre-flight antes de gastar una llamada: un JPG o un PDF corrupto no se
        # extrae, y el error del API sería mucho menos claro que este.
        raise ValueError("El archivo no parece un PDF (no empieza con %PDF)")

    if client is None:  # import perezoso: los tests no necesitan el SDK real
        import anthropic
        client = anthropic.Anthropic()

    data = base64.standard_b64encode(pdf_bytes).decode()
    respuesta = client.messages.parse(
        model=MODELO,
        # En claude-opus-5 el thinking es adaptativo por defecto (se deja así: es lo
        # recomendado) y max_tokens topa thinking + respuesta JUNTOS, así que un
        # presupuesto corto trunca el JSON de un 220 escaneado y el parse falla.
        max_tokens=16000,
        # Esto es transcripción mecánica de casillas, no razonamiento abierto: effort
        # "medium" gasta menos thinking sin cambiar el contrato del parse.
        output_config={"effort": "medium"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": data}},
                {"type": "text", "text": PROMPT_220},
            ],
        }],
        output_format=Extraccion220,
    )
    ext: Extraccion220 | None = respuesta.parsed_output
    if ext is None:
        # Sin salida estructurada: refusal de los clasificadores, max_tokens, u otro
        # stop_reason sin texto. Error de dominio explícito en vez del AttributeError
        # que saldría al leer el primer campo de None.
        raise ValueError(
            "La extracción del 220 no produjo salida estructurada "
            f"(stop_reason={respuesta.stop_reason})"
        )

    if ext.numero_de_certificados != 1:
        # Con dos certificados en el PDF no se sabe de cuál salió cada cifra.
        raise ValueError(
            f"El PDF contiene {ext.numero_de_certificados} certificados; "
            "procesa uno a la vez"
        )

    # Identidad del documento primero: el error más común es subir el 220 del año
    # equivocado, y este chequeo no puede falso-rechazar.
    if anio_esperado is not None and ext.anio_gravable != anio_esperado:
        raise ValueError(
            f"El certificado es del año gravable {ext.anio_gravable} "
            f"y se esperaba {anio_esperado}"
        )

    # Las pensiones entran en la suma porque el "Total de ingresos brutos" impreso las
    # incluye. Va ANTES del guard de pensiones a propósito: así el término es
    # load-bearing (un 220 mixto bien extraído reconcilia gracias a él) y por tanto
    # verificable, en vez de ser código correcto pero inalcanzable.
    suma = (
        ext.salarios + ext.cesantias_e_intereses + ext.prima + ext.bonificaciones
        + ext.pensiones_de_jubilacion
    )
    if abs(suma - ext.total_ingresos_brutos) > TOLERANCIA_RECONCILIACION_PESOS:
        # El total impreso es el testigo independiente: si los campos no lo reproducen,
        # el LLM se saltó una casilla o contó una dos veces. Si el total descuadra
        # tampoco se puede confiar en el campo de pensiones, así que este mensaje gana.
        raise ValueError(
            "la extracción no reconcilia contra el total impreso del certificado: "
            f"los campos suman {suma:,} y el certificado dice "
            f"{ext.total_ingresos_brutos:,}"
        )

    if ext.pensiones_de_jubilacion > 0:
        # La pensión se exime POR MES (IngresoPension.mesadas), no anual: registrarla
        # como laboral cambia el impuesto.
        raise ValueError(
            f"El 220 reporta pensiones ({ext.pensiones_de_jubilacion:,}); "
            "regístralas como IngresoPension, no laboral"
        )

    doc_id = hashlib.sha256(pdf_bytes).hexdigest()[:12]
    return IngresoLaboral(
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
