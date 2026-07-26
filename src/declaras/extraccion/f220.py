import base64
import hashlib

from pydantic import BaseModel, Field

from declaras.caso import Fuente, IngresoLaboral

MODELO = "claude-opus-5"

PROMPT_220 = """Este PDF es un Certificado de Ingresos y Retenciones (Formulario 220
de la DIAN, Colombia). Extrae los valores EXACTOS en pesos, sin puntos ni separadores.
Reglas:
- salarios: pagos por salarios (casilla 36 o equivalente del formato del año).
- cesantias_e_intereses, prima, bonificaciones: sus casillas respectivas; 0 si no aparecen.
- aportes_salud y aportes_pension: aportes OBLIGATORIOS del trabajador
  (pension incluye fondo de solidaridad si viene sumado).
- retencion: total retención en la fuente practicada en el año.
- empleador_nit sin dígito de verificación.
- confianza: tu confianza global 0.0-1.0 en la extracción (baja si el PDF es
  escaneado borroso o el formato es atípico)."""


class Extraccion220(BaseModel):
    empleador_nit: str
    empleador_nombre: str
    salarios: int = Field(ge=0)
    cesantias_e_intereses: int = Field(default=0, ge=0)
    prima: int = Field(default=0, ge=0)
    bonificaciones: int = Field(default=0, ge=0)
    aportes_salud: int = Field(ge=0)
    aportes_pension: int = Field(ge=0)
    retencion: int = Field(default=0, ge=0)
    confianza: float = Field(ge=0.0, le=1.0)


def extraer_220(pdf_bytes: bytes, client=None) -> IngresoLaboral:
    """Extrae un 220 con LLM y devuelve el hecho con proveniencia. Único punto con IA."""
    if client is None:  # import perezoso: los tests no necesitan el SDK real
        import anthropic
        client = anthropic.Anthropic()

    data = base64.standard_b64encode(pdf_bytes).decode()
    respuesta = client.messages.parse(
        model=MODELO,
        max_tokens=2048,
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
    ext: Extraccion220 = respuesta.parsed_output
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
