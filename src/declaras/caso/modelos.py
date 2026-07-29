from typing import Literal

from pydantic import Field, field_validator

from declaras.caso.fuentes import Fuente, MontoDeclarado, _Modelo

Monto = Field(ge=0)


class Contribuyente(_Modelo):
    tipo_doc: str = "CC"
    num_doc: str
    nombre: str
    residente: bool = True


class IngresoLaboral(_Modelo):
    empleador_nit: str
    empleador_nombre: str
    salarios: int = Monto
    cesantias_e_intereses: int = Field(default=0, ge=0)
    prima: int = Field(default=0, ge=0)
    bonificaciones: int = Field(default=0, ge=0)
    aportes_salud: int = Monto
    aportes_pension: int = Monto  # incluye fondo de solidaridad
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente

    @property
    def bruto(self) -> int:
        return self.salarios + self.cesantias_e_intereses + self.prima + self.bonificaciones


class IngresoPension(_Modelo):
    pagador: str
    # NIT opcional: el conciliador cruza por (NIT, concepto), asi que sin el la partida no
    # empareja con la fila de la exogena. Los extractores lo llenan siempre; la entrada
    # manual puede no tenerlo, y que falte lo señala el conciliador, no el schema.
    pagador_nit: str | None = None
    mesadas: list[int]  # 12 valores, enero a diciembre (la exención es POR MES)
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente

    @field_validator("mesadas")
    @classmethod
    def _doce_meses(cls, v: list[int]) -> list[int]:
        if len(v) != 12 or any(m < 0 for m in v):
            raise ValueError("mesadas debe tener exactamente 12 valores no negativos")
        return v


class Rendimiento(_Modelo):
    entidad: str
    entidad_nit: str | None = None  # opcional para el cruce, ver IngresoPension.pagador_nit
    valor: int = Monto
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente


class CostosArriendo(_Modelo):
    predial: int = Field(default=0, ge=0)
    administracion: int = Field(default=0, ge=0)
    comision_inmobiliaria: int = Field(default=0, ge=0)
    reparaciones: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.predial + self.administracion + self.comision_inmobiliaria + self.reparaciones


class Arriendo(_Modelo):
    inmueble: str
    contraparte_nombre: str | None = None  # quien paga el canon (o la inmobiliaria)
    contraparte_nit: str | None = None  # opcional para el cruce, ver IngresoPension.pagador_nit
    canon_total: int = Monto
    retencion: int = Field(default=0, ge=0)
    costos: CostosArriendo = CostosArriendo()
    fuente: Fuente


class Dividendo(_Modelo):
    sociedad_nit: str
    sociedad_nombre: str
    no_gravados: int = Field(default=0, ge=0)
    gravados: int = Field(default=0, ge=0)
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente


class Dependiente(_Modelo):
    tipo: Literal["hijo_menor", "hijo_estudiante", "hijo_discapacidad", "conyuge", "padre_hermano"]
    meses: int = Field(default=12, ge=1, le=12)
    fuente: Fuente


class AporteAfc(_Modelo):
    entidad: str
    tipo: Literal["AFC", "FVP"]
    valor: int = Monto
    fuente: Fuente


class Donacion(_Modelo):
    entidad: str
    valor: int = Monto
    certificada: bool = False
    fuente: Fuente


class Beneficios(_Modelo):
    dependientes: list[Dependiente] = []
    medicina_prepagada: MontoDeclarado | None = None
    intereses_vivienda: MontoDeclarado | None = None
    intereses_icetex: MontoDeclarado | None = None
    aportes_afc_fvp: list[AporteAfc] = []
    gmf_pagado: MontoDeclarado | None = None
    facturas_electronicas_total: MontoDeclarado | None = None
    donaciones_esal: list[Donacion] = []


class Activo(_Modelo):
    tipo: Literal["inmueble", "vehiculo", "cuenta", "inversion", "otro"]
    descripcion: str
    valor_31dic: int = Monto
    fuente: Fuente


class Deuda(_Modelo):
    acreedor: str
    saldo_31dic: int = Monto
    fuente: Fuente


class Patrimonio(_Modelo):
    activos: list[Activo] = []
    deudas: list[Deuda] = []
    patrimonio_liquido_anterior: int | None = None


class Movimientos(_Modelo):
    """Insumos para el chequeo de obligación (no son ingreso)."""

    consignaciones_totales: MontoDeclarado | None = None
    compras_y_consumos: MontoDeclarado | None = None


class Creditos(_Modelo):
    anticipo_pagado: int = Field(default=0, ge=0)
    saldo_favor_anterior: int = Field(default=0, ge=0)
    anios_previos_declarando: int = Field(default=0, ge=0)
    impuesto_neto_anio_anterior: int | None = Field(default=None, ge=0)


class CasoTributario(_Modelo):
    anio_gravable: int = 2025
    contribuyente: Contribuyente
    laborales: list[IngresoLaboral] = []
    pensiones: list[IngresoPension] = []
    rendimientos: list[Rendimiento] = []
    arriendos: list[Arriendo] = []
    dividendos: list[Dividendo] = []
    beneficios: Beneficios = Beneficios()
    patrimonio: Patrimonio = Patrimonio()
    movimientos: Movimientos = Movimientos()
    creditos: Creditos = Creditos()

    @property
    def ingresos_brutos_totales(self) -> int:
        return (
            sum(l.bruto for l in self.laborales)
            + sum(sum(pn.mesadas) for pn in self.pensiones)
            + sum(r.valor for r in self.rendimientos)
            + sum(a.canon_total for a in self.arriendos)
            + sum(d.no_gravados + d.gravados for d in self.dividendos)
        )
