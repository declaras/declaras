from decimal import Decimal

from pydantic import BaseModel

from declaras.dinero import pesos


class Tramo(BaseModel):
    desde_uvt: int
    hasta_uvt: int | None  # None = último tramo, sin tope
    tarifa: float


class ParametrosAnio(BaseModel):
    anio: int
    uvt: int
    uvt_siguiente: int
    tope_obligacion_ingresos_uvt: int
    tope_obligacion_patrimonio_uvt: int
    tope_obligacion_consignaciones_uvt: int
    limite_general_pct: float
    limite_general_uvt: int
    exenta_laboral_pct: float
    exenta_laboral_tope_uvt: int
    dependiente_uvt: int
    dependientes_max: int
    ded_387_pct: float
    ded_387_tope_uvt_mes: int
    prepagada_tope_uvt_anio: int
    intereses_vivienda_tope_uvt: int
    icetex_tope_uvt: int
    afc_pct: float
    afc_tope_uvt: int
    gmf_pct_deducible: float
    facturas_pct: float
    facturas_tope_uvt: int
    pension_exenta_uvt_mes: int
    dividendos_tarifa_gravados: float
    descuento_dividendos_pct: float
    descuento_dividendos_umbral_uvt: int
    donaciones_descuento_pct: float
    componente_inflacionario: float | None
    anticipo_pct: list[float]
    tabla_241: list[Tramo]

    def uvt_pesos(self, n: float) -> int:
        return pesos(Decimal(str(n)) * self.uvt)
