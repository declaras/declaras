# Demo declaras (backend) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Motor tributario AG 2025 completo (3 escenarios) + extractor 220 + API, según el spec `docs/specs/2026-07-25-demo-declaras-design.md`.

**Architecture:** Pipeline extracción (LLM) → Caso Tributario (pydantic, hechos con proveniencia) → motor determinístico + optimizador (config por año) → Liquidación trazable → render casilla por casilla. FastAPI encima; almacenamiento JSON en disco (sin DB para el demo).

**Tech Stack:** Python 3.13, uv, pydantic v2, pytest, FastAPI, Jinja2, PyYAML, SDK `anthropic` (solo en `extraccion/`).

## Global Constraints

- Dinero: **int en pesos COP**. Todo redondeo con `pesos()` (ROUND_HALF_UP). Nunca floats en resultados.
- UVT 2025 = **49.799**; UVT 2026 = **52.374**. Toda regla referencia parámetros por año gravable, jamás constantes en el motor.
- El motor es **puro y determinista**: cero LLM, cero `datetime.now()`, cero I/O. LLM solo en `src/declaras/extraccion/`.
- Identificadores de dominio **en español** (`rlg_general`, `mesadas`, `retencion`) — los términos tributarios no se traducen.
- El Caso contiene **hechos, nunca conclusiones**; cada registro lleva `fuente: Fuente`.
- Códigos de nodo (contrato entre motor, tests y render): `ING_BRUTO_GENERAL, INCR_APORTES, INCR_CI, INCR_TOTAL, ING_NETOS_GENERAL, COSTOS_ARRIENDOS, CAP_40, DEDUCCIONES_LIMITADAS, EXENTA_25, APLICADO_40, EXTRA_LIMITE, RLG_GENERAL, RLG_PENSIONES, DIV_NO_GRAVADOS, DIV_GRAVADOS, IMP_DIV_35, BASE_TABLA_241, IMPUESTO_241, DESCUENTO_254_1, DESCUENTO_DONACIONES, IMPUESTO_NETO, RETENCIONES, ANTICIPO_SIGUIENTE, SALDO, OBLIGADO_DECLARAR, PATRIMONIO_BRUTO, PATRIMONIO_LIQUIDO`.
- Flags: `COMPONENTE_INFLACIONARIO_PROVISIONAL`, `COMPARACION_PATRIMONIAL`, `NO_OBLIGADO`.
- Commits: mensaje en español, terminar con `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Fuera de este plan: repo `front` (plan aparte contra el API real), PDF nativo (el HTML del render es imprimible), extractores de exógena/certificados (v1.1).

---

### Task 1: Scaffold del backend

**Files:**
- Create: `pyproject.toml`, `src/declaras/__init__.py`, `tests/test_smoke.py`, `.gitignore`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: paquete `declaras` importable; `uv run pytest` verde; CI en GitHub Actions.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_smoke.py
def test_importa():
    import declaras
    assert declaras.__version__ == "0.1.0"
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `cd ~/Desktop/declaras/back && uv run pytest -q`
Expected: error — no existe pyproject/paquete.

- [ ] **Step 3: Implementación mínima**

```toml
# pyproject.toml
[project]
name = "declaras"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.7",
    "pyyaml>=6",
    "fastapi>=0.111",
    "uvicorn>=0.30",
    "jinja2>=3.1",
    "anthropic>=0.100",
    "python-multipart>=0.0.9",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/declaras"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# src/declaras/__init__.py
__version__ = "0.1.0"
```

```gitignore
# .gitignore
__pycache__/
*.pyc
.venv/
var/
.pytest_cache/
.env
```

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest -q
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv sync && uv run pytest -q`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src tests .gitignore .github uv.lock
git commit -m "Scaffold del backend: uv + pydantic + pytest + CI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Dinero, parámetros AG 2025 y tabla 241

**Files:**
- Create: `src/declaras/dinero.py`, `src/declaras/parametros/__init__.py`, `src/declaras/parametros/modelos.py`, `src/declaras/parametros/tabla.py`, `src/declaras/parametros/ag2025.yaml`
- Test: `tests/test_parametros.py`

**Interfaces:**
- Produces: `pesos(x) -> int` (half-up); `cargar(anio: int) -> ParametrosAnio`; `ParametrosAnio.uvt_pesos(n: float) -> int`; `impuesto_tabla_241(base: int, p: ParametrosAnio) -> int`.
- `ParametrosAnio` campos (todos obligatorios salvo nota): `anio, uvt, uvt_siguiente, tope_obligacion_ingresos_uvt, tope_obligacion_patrimonio_uvt, tope_obligacion_consignaciones_uvt, limite_general_pct, limite_general_uvt, exenta_laboral_pct, exenta_laboral_tope_uvt, dependiente_uvt, dependientes_max, ded_387_pct, ded_387_tope_uvt_mes, prepagada_tope_uvt_anio, intereses_vivienda_tope_uvt, icetex_tope_uvt, afc_pct, afc_tope_uvt, gmf_pct_deducible, facturas_pct, facturas_tope_uvt, pension_exenta_uvt_mes, dividendos_tarifa_gravados, descuento_dividendos_pct, descuento_dividendos_umbral_uvt, donaciones_descuento_pct, componente_inflacionario: float | None, anticipo_pct: list[float], tabla_241: list[Tramo]`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_parametros.py
from declaras.dinero import pesos
from declaras.parametros import cargar
from declaras.parametros.tabla import impuesto_tabla_241


def test_pesos_half_up():
    assert pesos(1495976.78) == 1495977
    assert pesos(373994.25) == 373994
    assert pesos(0.5) == 1
    assert pesos(10) == 10


def test_carga_ag2025():
    p = cargar(2025)
    assert p.uvt == 49799
    assert p.uvt_pesos(1340) == 66_730_660
    assert p.uvt_pesos(1090) == 54_280_910
    assert p.uvt_pesos(790) == 39_341_210
    assert p.componente_inflacionario is None  # pendiente decreto


def test_tabla_241():
    p = cargar(2025)
    assert impuesto_tabla_241(0, p) == 0
    assert impuesto_tabla_241(54_280_910, p) == 0            # exacto en 1.090 UVT
    assert impuesto_tabla_241(62_154_472, p) == 1_495_977    # tramo 19% (constante 0)
    assert impuesto_tabla_241(125_212_000, p) == 17_131_720  # tramo 28% + 116 UVT
    assert impuesto_tabla_241(118_978_944, p) == 15_386_464
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_parametros.py -q`
Expected: FAIL — módulos inexistentes.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/dinero.py
from decimal import ROUND_HALF_UP, Decimal


def pesos(x) -> int:
    """Redondea a peso entero con half-up (0.5 sube). Único punto de redondeo del sistema."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

```python
# src/declaras/parametros/modelos.py
from decimal import Decimal

from pydantic import BaseModel

from declaras.dinero import pesos


class Tramo(BaseModel):
    desde_uvt: int
    hasta_uvt: int | None  # None = último tramo, sin tope
    tarifa: float
    constante_uvt: int = 0  # constante publicada del art. 241, en UVT (116, 788, ...)


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
```

```yaml
# src/declaras/parametros/ag2025.yaml
anio: 2025
uvt: 49799
uvt_siguiente: 52374
tope_obligacion_ingresos_uvt: 1400
tope_obligacion_patrimonio_uvt: 4500
tope_obligacion_consignaciones_uvt: 1400
limite_general_pct: 0.40
limite_general_uvt: 1340
exenta_laboral_pct: 0.25
exenta_laboral_tope_uvt: 790
dependiente_uvt: 72
dependientes_max: 4
ded_387_pct: 0.10
ded_387_tope_uvt_mes: 32
prepagada_tope_uvt_anio: 192
intereses_vivienda_tope_uvt: 1200
icetex_tope_uvt: 100
afc_pct: 0.30
afc_tope_uvt: 3800
gmf_pct_deducible: 0.50
facturas_pct: 0.01
facturas_tope_uvt: 240
pension_exenta_uvt_mes: 1000
dividendos_tarifa_gravados: 0.35
descuento_dividendos_pct: 0.19
descuento_dividendos_umbral_uvt: 1090
donaciones_descuento_pct: 0.25
# PENDIENTE: decreto AG 2025 (sale ~mitad de 2026). null activa el flag provisional.
componente_inflacionario: null
anticipo_pct: [0.25, 0.50, 0.75]
# Fórmula publicada del art. 241: (base − desde) × tarifa + constante_uvt
tabla_241:
  - {desde_uvt: 0,     hasta_uvt: 1090,  tarifa: 0.0,  constante_uvt: 0}
  - {desde_uvt: 1090,  hasta_uvt: 1700,  tarifa: 0.19, constante_uvt: 0}
  - {desde_uvt: 1700,  hasta_uvt: 4100,  tarifa: 0.28, constante_uvt: 116}
  - {desde_uvt: 4100,  hasta_uvt: 8670,  tarifa: 0.33, constante_uvt: 788}
  - {desde_uvt: 8670,  hasta_uvt: 18970, tarifa: 0.35, constante_uvt: 2296}
  - {desde_uvt: 18970, hasta_uvt: 31000, tarifa: 0.37, constante_uvt: 5901}
  - {desde_uvt: 31000, hasta_uvt: null,  tarifa: 0.39, constante_uvt: 10352}
```

```python
# src/declaras/parametros/__init__.py
from pathlib import Path

import yaml

from declaras.parametros.modelos import ParametrosAnio, Tramo

__all__ = ["ParametrosAnio", "Tramo", "cargar"]

_DIR = Path(__file__).parent


def cargar(anio: int) -> ParametrosAnio:
    ruta = _DIR / f"ag{anio}.yaml"
    if not ruta.exists():
        raise ValueError(f"No hay parámetros para el año gravable {anio}")
    return ParametrosAnio.model_validate(yaml.safe_load(ruta.read_text()))
```

```python
# src/declaras/parametros/tabla.py
from decimal import Decimal

from declaras.dinero import pesos
from declaras.parametros.modelos import ParametrosAnio


def impuesto_tabla_241(base: int, p: ParametrosAnio) -> int:
    """Impuesto del art. 241 ET con la fórmula publicada en la ley:
    (base − límite inferior del tramo) × tarifa + constante del tramo en UVT."""
    if base <= 0:
        return 0
    for tramo in p.tabla_241:
        desde = tramo.desde_uvt * p.uvt
        hasta = tramo.hasta_uvt * p.uvt if tramo.hasta_uvt is not None else None
        if base > desde and (hasta is None or base <= hasta):
            return pesos(Decimal(base - desde) * Decimal(str(tramo.tarifa))
                         + tramo.constante_uvt * p.uvt)
    return 0
```

Nota de empaque: agregar a `pyproject.toml` para que el YAML viaje con el paquete:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/declaras/parametros/ag2025.yaml" = "declaras/parametros/ag2025.yaml"
```

(Con hatchling y `packages = ["src/declaras"]` los data files dentro del paquete se incluyen por defecto; si el test pasa sin el force-include, omitirlo.)

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_parametros.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/dinero.py src/declaras/parametros tests/test_parametros.py pyproject.toml
git commit -m "Parámetros AG 2025, redondeo half-up y tabla marginal art. 241

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Schema del Caso Tributario

**Files:**
- Create: `src/declaras/caso/__init__.py`, `src/declaras/caso/fuentes.py`, `src/declaras/caso/modelos.py`
- Test: `tests/test_caso.py`

**Interfaces:**
- Produces: `Fuente` (con classmethods `Fuente.manual(quien)`, `Fuente.fixture(nombre)`, `Fuente.documento(tipo, doc_id, pagina=None, confianza=None)`), `MontoDeclarado{valor:int, fuente:Fuente}`, y los modelos: `Contribuyente, IngresoLaboral, IngresoPension, Rendimiento, Arriendo, CostosArriendo, Dividendo, Dependiente, AporteAfc, Donacion, Beneficios, Activo, Deuda, Patrimonio, Movimientos, Creditos, CasoTributario`.
- Reglas de validación: `IngresoPension.mesadas` exactamente 12 elementos; todos los montos `ge=0`.
- Propiedades: `IngresoLaboral.bruto` (salarios+cesantías+prima+bonificaciones), `CasoTributario.ingresos_brutos_totales`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_caso.py
import pytest
from pydantic import ValidationError

from declaras.caso import (
    CasoTributario, Contribuyente, Fuente, IngresoLaboral, IngresoPension,
)

FX = Fuente.fixture("test")


def _laboral(**kw):
    base = dict(
        empleador_nit="900123456", empleador_nombre="ACME SAS",
        salarios=120_000_000, aportes_salud=4_800_000,
        aportes_pension=4_800_000, retencion=8_000_000, fuente=FX,
    )
    base.update(kw)
    return IngresoLaboral(**base)


def test_bruto_laboral_suma_componentes():
    lab = _laboral(cesantias_e_intereses=2_000_000, prima=1_000_000)
    assert lab.bruto == 123_000_000


def test_pension_exige_12_mesadas():
    with pytest.raises(ValidationError):
        IngresoPension(pagador="Colpensiones", mesadas=[10_000_000] * 11, fuente=FX)


def test_montos_no_negativos():
    with pytest.raises(ValidationError):
        _laboral(salarios=-1)


def test_caso_minimo_e_ingresos_totales():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="1234567", nombre="Prueba"),
        laborales=[_laboral()],
    )
    assert caso.anio_gravable == 2025
    assert caso.ingresos_brutos_totales == 120_000_000
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_caso.py -q`
Expected: FAIL — módulo `declaras.caso` inexistente.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/caso/fuentes.py
from typing import Literal

from pydantic import BaseModel


class Fuente(BaseModel):
    """Proveniencia de un hecho: de dónde salió y con qué confianza."""

    clase: Literal["documento", "manual", "fixture", "exogena"]
    ref: str
    detalle: str | None = None
    confianza: float | None = None

    @classmethod
    def manual(cls, quien: str) -> "Fuente":
        return cls(clase="manual", ref=quien)

    @classmethod
    def fixture(cls, nombre: str) -> "Fuente":
        return cls(clase="fixture", ref=nombre)

    @classmethod
    def documento(cls, tipo: str, doc_id: str, pagina: int | None = None,
                  confianza: float | None = None) -> "Fuente":
        detalle = f"{tipo} pág {pagina}" if pagina else tipo
        return cls(clase="documento", ref=doc_id, detalle=detalle, confianza=confianza)


class MontoDeclarado(BaseModel):
    valor: int
    fuente: Fuente
```

```python
# src/declaras/caso/modelos.py
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from declaras.caso.fuentes import Fuente, MontoDeclarado

Monto = Field(ge=0)


class Contribuyente(BaseModel):
    tipo_doc: str = "CC"
    num_doc: str
    nombre: str
    residente: bool = True


class IngresoLaboral(BaseModel):
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


class IngresoPension(BaseModel):
    pagador: str
    mesadas: list[int]  # 12 valores, enero a diciembre (la exención es POR MES)
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente

    @field_validator("mesadas")
    @classmethod
    def _doce_meses(cls, v: list[int]) -> list[int]:
        if len(v) != 12 or any(m < 0 for m in v):
            raise ValueError("mesadas debe tener exactamente 12 valores no negativos")
        return v


class Rendimiento(BaseModel):
    entidad: str
    valor: int = Monto
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente


class CostosArriendo(BaseModel):
    predial: int = Field(default=0, ge=0)
    administracion: int = Field(default=0, ge=0)
    comision_inmobiliaria: int = Field(default=0, ge=0)
    reparaciones: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.predial + self.administracion + self.comision_inmobiliaria + self.reparaciones


class Arriendo(BaseModel):
    inmueble: str
    canon_total: int = Monto
    retencion: int = Field(default=0, ge=0)
    costos: CostosArriendo = CostosArriendo()
    fuente: Fuente


class Dividendo(BaseModel):
    sociedad_nit: str
    sociedad_nombre: str
    no_gravados: int = Field(default=0, ge=0)
    gravados: int = Field(default=0, ge=0)
    retencion: int = Field(default=0, ge=0)
    fuente: Fuente


class Dependiente(BaseModel):
    tipo: Literal["hijo_menor", "hijo_estudiante", "hijo_discapacidad",
                  "conyuge", "padre_hermano"]
    meses: int = Field(default=12, ge=1, le=12)
    fuente: Fuente


class AporteAfc(BaseModel):
    entidad: str
    tipo: Literal["AFC", "FVP"]
    valor: int = Monto
    fuente: Fuente


class Donacion(BaseModel):
    entidad: str
    valor: int = Monto
    certificada: bool = False
    fuente: Fuente


class Beneficios(BaseModel):
    dependientes: list[Dependiente] = []
    medicina_prepagada: MontoDeclarado | None = None
    intereses_vivienda: MontoDeclarado | None = None
    intereses_icetex: MontoDeclarado | None = None
    aportes_afc_fvp: list[AporteAfc] = []
    gmf_pagado: MontoDeclarado | None = None
    facturas_electronicas_total: MontoDeclarado | None = None
    donaciones_esal: list[Donacion] = []


class Activo(BaseModel):
    tipo: Literal["inmueble", "vehiculo", "cuenta", "inversion", "otro"]
    descripcion: str
    valor_31dic: int = Monto
    fuente: Fuente


class Deuda(BaseModel):
    acreedor: str
    saldo_31dic: int = Monto
    fuente: Fuente


class Patrimonio(BaseModel):
    activos: list[Activo] = []
    deudas: list[Deuda] = []
    patrimonio_liquido_anterior: int | None = None


class Movimientos(BaseModel):
    """Insumos para el chequeo de obligación (no son ingreso)."""

    consignaciones_totales: MontoDeclarado | None = None
    compras_y_consumos: MontoDeclarado | None = None


class Creditos(BaseModel):
    anticipo_pagado: int = Field(default=0, ge=0)
    saldo_favor_anterior: int = Field(default=0, ge=0)
    anios_previos_declarando: int = Field(default=0, ge=0)
    impuesto_neto_anio_anterior: int | None = None


class CasoTributario(BaseModel):
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
```

```python
# src/declaras/caso/__init__.py
from declaras.caso.fuentes import Fuente, MontoDeclarado
from declaras.caso.modelos import (
    Activo, AporteAfc, Arriendo, Beneficios, CasoTributario, Contribuyente,
    CostosArriendo, Creditos, Dependiente, Deuda, Dividendo, Donacion,
    IngresoLaboral, IngresoPension, Movimientos, Patrimonio, Rendimiento,
)

__all__ = [
    "Activo", "AporteAfc", "Arriendo", "Beneficios", "CasoTributario",
    "Contribuyente", "CostosArriendo", "Creditos", "Dependiente", "Deuda",
    "Dividendo", "Donacion", "Fuente", "IngresoLaboral", "IngresoPension",
    "MontoDeclarado", "Movimientos", "Patrimonio", "Rendimiento",
]
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_caso.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/caso tests/test_caso.py
git commit -m "Schema del Caso Tributario: hechos con proveniencia, modular por tipo de ingreso

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Traza, Liquidación y Elecciones

**Files:**
- Create: `src/declaras/motor/__init__.py` (vacío por ahora), `src/declaras/motor/traza.py`, `src/declaras/motor/elecciones.py`
- Test: `tests/test_traza.py`

**Interfaces:**
- Produces: `Nodo{codigo, etiqueta, valor:int, formula:str, insumos:list[str], regla:str|None}`; `Flag{codigo, mensaje, severidad}`; `Traza` con `.nodo(codigo, etiqueta, valor: int, formula, insumos=(), regla=None) -> int` (valida int vía pydantic — ValidationError si no lo es —, ValueError si el código ya está registrado, devuelve el int validado), `.flag(codigo, mensaje, severidad="advertencia")`, `.a_liquidacion(anio, elecciones) -> Liquidacion`; `Liquidacion{anio_gravable, elecciones, nodos:dict[str,Nodo], flags:list[Flag]}` con `.valor(codigo) -> int` y `.tiene_flag(codigo) -> bool`; `Elecciones{usar_387:bool=False, usar_72uvt:bool=True}`. Todos los modelos con `extra="forbid"`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_traza.py
from declaras.motor.elecciones import Elecciones
from declaras.motor.traza import Traza


def test_nodo_registra_y_devuelve():
    t = Traza()
    v = t.nodo("A", "Ingreso", 100, "suma de x", insumos=["hecho:laboral[0]"])
    assert v == 100
    assert t.nodos["A"].formula == "suma de x"


def test_liquidacion_lookup_y_flags():
    t = Traza()
    t.nodo("A", "Ingreso", 100, "x")
    t.flag("PRUEBA", "algo por revisar")
    liq = t.a_liquidacion(2025, Elecciones())
    assert liq.valor("A") == 100
    assert liq.tiene_flag("PRUEBA")
    assert not liq.tiene_flag("OTRA")
    assert liq.elecciones.usar_72uvt is True
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_traza.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/motor/elecciones.py
from pydantic import BaseModel


class Elecciones(BaseModel):
    """Decisiones legales abiertas que el optimizador enumera."""

    usar_387: bool = False    # 10% art. 387 (dentro del límite 40%)
    usar_72uvt: bool = True   # 72 UVT por dependiente (extra-límite)

    @property
    def activas(self) -> int:
        return int(self.usar_387) + int(self.usar_72uvt)
```

```python
# src/declaras/motor/traza.py
from typing import Literal

from pydantic import BaseModel

from declaras.motor.elecciones import Elecciones


class Nodo(BaseModel):
    codigo: str
    etiqueta: str
    valor: int
    formula: str
    insumos: list[str] = []
    regla: str | None = None


class Flag(BaseModel):
    codigo: str
    mensaje: str
    severidad: Literal["info", "advertencia", "bloqueante"] = "advertencia"


class Liquidacion(BaseModel):
    anio_gravable: int
    elecciones: Elecciones
    nodos: dict[str, Nodo]
    flags: list[Flag]

    def valor(self, codigo: str) -> int:
        return self.nodos[codigo].valor

    def tiene_flag(self, codigo: str) -> bool:
        return any(f.codigo == codigo for f in self.flags)


class Traza:
    """Acumulador del árbol de cálculo. Cada casilla queda con fórmula e insumos."""

    def __init__(self) -> None:
        self.nodos: dict[str, Nodo] = {}
        self.flags: list[Flag] = []

    def nodo(self, codigo: str, etiqueta: str, valor, formula: str,
             insumos=(), regla: str | None = None) -> int:
        v = int(valor)
        self.nodos[codigo] = Nodo(codigo=codigo, etiqueta=etiqueta, valor=v,
                                  formula=formula, insumos=list(insumos), regla=regla)
        return v

    def flag(self, codigo: str, mensaje: str,
             severidad: Literal["info", "advertencia", "bloqueante"] = "advertencia") -> None:
        self.flags.append(Flag(codigo=codigo, mensaje=mensaje, severidad=severidad))

    def a_liquidacion(self, anio: int, elecciones: Elecciones) -> Liquidacion:
        return Liquidacion(anio_gravable=anio, elecciones=elecciones,
                           nodos=dict(self.nodos), flags=list(self.flags))
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_traza.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor tests/test_traza.py
git commit -m "Traza de cálculo, Liquidación y Elecciones del optimizador

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Motor — base de la cédula general (ingresos, INCR, costos)

**Files:**
- Create: `src/declaras/motor/general.py`
- Test: `tests/test_general_base.py`

**Interfaces:**
- Consumes: `CasoTributario`, `ParametrosAnio`, `Traza`, `pesos`.
- Produces: `base_general(caso, p, t) -> None` — registra los nodos `ING_BRUTO_GENERAL, INCR_APORTES, INCR_CI, INCR_TOTAL, ING_NETOS_GENERAL, COSTOS_ARRIENDOS, CAP_40`. Si `p.componente_inflacionario is None` y hay rendimientos, `INCR_CI=0` + flag `COMPONENTE_INFLACIONARIO_PROVISIONAL`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_general_base.py
from declaras.caso import (
    Arriendo, CasoTributario, Contribuyente, CostosArriendo, Fuente,
    IngresoLaboral, Rendimiento,
)
from declaras.motor.general import base_general
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(**kw):
    base = dict(contribuyente=Contribuyente(num_doc="1", nombre="X"))
    base.update(kw)
    return CasoTributario(**base)


LABORAL = IngresoLaboral(
    empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
    aportes_salud=4_800_000, aportes_pension=4_800_000, retencion=8_000_000, fuente=FX,
)


def test_solo_laboral():
    t = Traza()
    base_general(_caso(laborales=[LABORAL]), P, t)
    assert t.nodos["ING_BRUTO_GENERAL"].valor == 120_000_000
    assert t.nodos["INCR_TOTAL"].valor == 9_600_000
    assert t.nodos["ING_NETOS_GENERAL"].valor == 110_400_000
    assert t.nodos["CAP_40"].valor == 44_160_000  # 40% < 1.340 UVT


def test_rendimientos_con_ci_provisional():
    t = Traza()
    caso = _caso(rendimientos=[Rendimiento(entidad="Banco", valor=8_000_000,
                                           retencion=560_000, fuente=FX)])
    base_general(caso, P, t)
    assert t.nodos["INCR_CI"].valor == 0
    assert any(f.codigo == "COMPONENTE_INFLACIONARIO_PROVISIONAL" for f in t.flags)


def test_arriendos_restan_costos():
    t = Traza()
    caso = _caso(arriendos=[Arriendo(
        inmueble="Apto 101", canon_total=36_000_000, retencion=1_260_000,
        costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                              comision_inmobiliaria=3_600_000), fuente=FX)])
    base_general(caso, P, t)
    assert t.nodos["ING_BRUTO_GENERAL"].valor == 36_000_000
    assert t.nodos["COSTOS_ARRIENDOS"].valor == 11_400_000
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_general_base.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/motor/general.py  (parte 1: base)
from declaras.caso import CasoTributario
from declaras.dinero import pesos
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def base_general(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> None:
    """Pasos 1-2 del art. 336: ingresos brutos, INCRNGO y base del límite 40%."""
    bruto_laboral = sum(l.bruto for l in caso.laborales)
    total_rend = sum(r.valor for r in caso.rendimientos)
    total_arriendos = sum(a.canon_total for a in caso.arriendos)

    bruto = t.nodo(
        "ING_BRUTO_GENERAL", "Ingresos brutos cédula general",
        bruto_laboral + total_rend + total_arriendos,
        f"laborales {bruto_laboral:,} + rendimientos {total_rend:,} + arriendos {total_arriendos:,}",
        regla="art. 335 ET",
    )

    aportes = t.nodo(
        "INCR_APORTES", "INCRNGO aportes obligatorios salud/pensión",
        sum(l.aportes_salud + l.aportes_pension for l in caso.laborales),
        "suma aportes obligatorios de cada 220",
        regla="arts. 55-56 ET",
    )

    if total_rend and p.componente_inflacionario is None:
        t.flag(
            "COMPONENTE_INFLACIONARIO_PROVISIONAL",
            "El decreto del componente inflacionario AG 2025 no ha salido: "
            "se usa 0% (conservador). Actualizar ag2025.yaml cuando se expida.",
        )
    pct_ci = p.componente_inflacionario or 0.0
    ci = t.nodo(
        "INCR_CI", "INCRNGO componente inflacionario de rendimientos",
        pesos(total_rend * pct_ci),
        f"{pct_ci:.2%} × rendimientos {total_rend:,}",
        regla="arts. 38-41 ET",
    )

    incr = t.nodo("INCR_TOTAL", "Total INCRNGO", aportes + ci,
                  "INCR_APORTES + INCR_CI", insumos=["INCR_APORTES", "INCR_CI"])

    netos = t.nodo("ING_NETOS_GENERAL", "Ingresos netos (base del límite 40%)",
                   bruto - incr, "ING_BRUTO_GENERAL − INCR_TOTAL",
                   insumos=["ING_BRUTO_GENERAL", "INCR_TOTAL"], regla="art. 336 num. 3")

    t.nodo("COSTOS_ARRIENDOS", "Costos y gastos procedentes de arriendos",
           sum(a.costos.total for a in caso.arriendos),
           "predial + administración + comisión + reparaciones (con soporte)",
           regla="art. 336 num. 4")

    t.nodo("CAP_40", "Límite exentas+deducciones (menor entre 40% y 1.340 UVT)",
           min(pesos(netos * p.limite_general_pct), p.uvt_pesos(p.limite_general_uvt)),
           f"min(40% × {netos:,}, 1.340 UVT = {p.uvt_pesos(p.limite_general_uvt):,})",
           insumos=["ING_NETOS_GENERAL"], regla="art. 336 num. 3")
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_general_base.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor/general.py tests/test_general_base.py
git commit -m "Motor: base de la cédula general (brutos, INCRNGO, costos, límite 40%)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Motor — deducciones, 25% exento, extra-límite y RLG general

**Files:**
- Modify: `src/declaras/motor/general.py` (agregar `rlg_general`)
- Test: `tests/test_rlg_general.py`

**Interfaces:**
- Consumes: `base_general` (debe haberse llamado antes sobre la misma `Traza`), `Elecciones`.
- Produces: `rlg_general(caso, p, e, t) -> int` — registra `DEDUCCIONES_LIMITADAS, EXENTA_25, APLICADO_40, EXTRA_LIMITE, RLG_GENERAL` y devuelve el valor de `RLG_GENERAL`.
- Interpretación documentada (I-1, validar con el contador): la base del 25% = bruto laboral − INCR aportes − deducciones imputables al trabajo (intereses vivienda + prepagada + Icetex + 387 + AFC). GMF **no** entra a esa base; 72 UVT y 1% facturas tampoco (extra-límite, num. 3 y 5 del 336).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_rlg_general.py
from declaras.caso import (
    Arriendo, Beneficios, CasoTributario, Contribuyente, CostosArriendo,
    Dependiente, Fuente, IngresoLaboral, MontoDeclarado,
)
from declaras.motor.elecciones import Elecciones
from declaras.motor.general import base_general, rlg_general
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _md(v):
    return MontoDeclarado(valor=v, fuente=FX)


def caso_g1():
    """Asalariado 120M con beneficios: el límite 40% se copa."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="G1"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
            aportes_salud=4_800_000, aportes_pension=4_800_000,
            retencion=8_000_000, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX)],
            medicina_prepagada=_md(6_000_000),
            intereses_vivienda=_md(18_000_000),
            gmf_pagado=_md(1_000_000),
            facturas_electronicas_total=_md(50_000_000),
        ),
    )


def caso_g3_parcial():
    """Asalariado 100M + arriendo: el límite NO se copa, el 387 sí paga."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="3", nombre="G3"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=100_000_000,
            aportes_salud=4_000_000, aportes_pension=4_000_000,
            retencion=6_000_000, fuente=FX)],
        arriendos=[Arriendo(
            inmueble="Apto", canon_total=36_000_000, retencion=1_260_000,
            costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                                  comision_inmobiliaria=3_600_000), fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX),
                          Dependiente(tipo="hijo_estudiante", fuente=FX)],
            gmf_pagado=_md(900_000),
        ),
    )


def _rlg(caso, e):
    t = Traza()
    base_general(caso, P, t)
    return rlg_general(caso, P, e, t), t


def test_g1_cap_copado_ambas_elecciones_igual():
    v_sin, t = _rlg(caso_g1(), Elecciones(usar_387=False, usar_72uvt=True))
    v_con, _ = _rlg(caso_g1(), Elecciones(usar_387=True, usar_72uvt=True))
    assert v_sin == v_con == 62_154_472
    assert t.nodos["APLICADO_40"].valor == 44_160_000       # cap manda
    assert t.nodos["EXTRA_LIMITE"].valor == 4_085_528       # 72 UVT + 1% facturas


def test_g1_sin_72uvt():
    v, _ = _rlg(caso_g1(), Elecciones(usar_387=False, usar_72uvt=False))
    assert v == 65_740_000  # extra-límite = solo 1% facturas (500.000)


def test_g3_cap_no_copado_387_paga():
    v_con, t = _rlg(caso_g3_parcial(), Elecciones(usar_387=True, usar_72uvt=True))
    v_sin, _ = _rlg(caso_g3_parcial(), Elecciones(usar_387=False, usar_72uvt=True))
    assert v_con == 82_478_944
    assert v_sin == 89_978_944
    assert t.nodos["EXTRA_LIMITE"].valor == 7_171_056  # 72 UVT × 2 dependientes
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_rlg_general.py -q`
Expected: FAIL — `rlg_general` no existe.

- [ ] **Step 3: Implementación mínima** (agregar al final de `general.py`)

```python
# src/declaras/motor/general.py  (parte 2: agregar imports y función)
from declaras.motor.elecciones import Elecciones  # (junto a los demás imports)


def rlg_general(caso: CasoTributario, p: ParametrosAnio, e: Elecciones, t: Traza) -> int:
    """Pasos 3-5 del art. 336: deducciones/exentas con límite, extra-límite y RLG."""
    bruto_laboral = sum(l.bruto for l in caso.laborales)
    incr_aportes = t.nodos["INCR_APORTES"].valor
    b = caso.beneficios

    intereses = min(b.intereses_vivienda.valor if b.intereses_vivienda else 0,
                    p.uvt_pesos(p.intereses_vivienda_tope_uvt))
    prepagada = min(b.medicina_prepagada.valor if b.medicina_prepagada else 0,
                    p.uvt_pesos(p.prepagada_tope_uvt_anio))
    icetex = min(b.intereses_icetex.valor if b.intereses_icetex else 0,
                 p.uvt_pesos(p.icetex_tope_uvt))
    gmf = pesos((b.gmf_pagado.valor if b.gmf_pagado else 0) * p.gmf_pct_deducible)
    afc = min(sum(a.valor for a in b.aportes_afc_fvp),
              pesos(t.nodos["ING_BRUTO_GENERAL"].valor * p.afc_pct),
              p.uvt_pesos(p.afc_tope_uvt))
    ded_387 = 0
    if e.usar_387 and b.dependientes:
        ded_387 = min(pesos(bruto_laboral * p.ded_387_pct),
                      p.uvt_pesos(p.ded_387_tope_uvt_mes * 12))

    deducciones = t.nodo(
        "DEDUCCIONES_LIMITADAS", "Deducciones dentro del límite 40%",
        intereses + prepagada + icetex + gmf + afc + ded_387,
        f"vivienda {intereses:,} + prepagada {prepagada:,} + icetex {icetex:,} "
        f"+ GMF50% {gmf:,} + AFC/FVP {afc:,} + art387 {ded_387:,}",
        regla="arts. 119, 126-1, 126-4, 115, 387 ET",
    )

    # Interpretación I-1: base del 25% excluye GMF, 72 UVT y 1% (validar con contador).
    base_25 = max(0, bruto_laboral - incr_aportes
                  - (intereses + prepagada + icetex + ded_387 + afc))
    exenta_25 = t.nodo(
        "EXENTA_25", "Renta exenta 25% laboral (tope 790 UVT)",
        min(pesos(base_25 * p.exenta_laboral_pct),
            p.uvt_pesos(p.exenta_laboral_tope_uvt)),
        f"min(25% × base {base_25:,}, 790 UVT)", regla="art. 206 num. 10 ET",
    )

    cap = t.nodos["CAP_40"].valor
    aplicado = t.nodo(
        "APLICADO_40", "Exentas + deducciones aplicadas (tras el límite)",
        min(deducciones + exenta_25, cap),
        f"min({deducciones:,} + {exenta_25:,}, cap {cap:,})",
        insumos=["DEDUCCIONES_LIMITADAS", "EXENTA_25", "CAP_40"],
        regla="art. 336 num. 3",
    )

    dep_72 = 0
    if e.usar_72uvt and b.dependientes:
        n = min(len(b.dependientes), p.dependientes_max)
        dep_72 = p.uvt_pesos(p.dependiente_uvt * n)
    fact_1 = min(
        pesos((b.facturas_electronicas_total.valor
               if b.facturas_electronicas_total else 0) * p.facturas_pct),
        p.uvt_pesos(p.facturas_tope_uvt),
    )
    extra = t.nodo("EXTRA_LIMITE", "Beneficios por fuera del límite 40%",
                   dep_72 + fact_1,
                   f"72 UVT dependientes {dep_72:,} + 1% facturas {fact_1:,}",
                   regla="art. 336 num. 3 y 5")

    netos = t.nodos["ING_NETOS_GENERAL"].valor
    costos = t.nodos["COSTOS_ARRIENDOS"].valor
    return t.nodo(
        "RLG_GENERAL", "Renta líquida gravable cédula general",
        max(0, netos - costos - aplicado - extra),
        f"{netos:,} − costos {costos:,} − aplicado {aplicado:,} − extra {extra:,}",
        insumos=["ING_NETOS_GENERAL", "COSTOS_ARRIENDOS", "APLICADO_40", "EXTRA_LIMITE"],
        regla="art. 336 ET",
    )
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_rlg_general.py tests/test_general_base.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor/general.py tests/test_rlg_general.py
git commit -m "Motor: deducciones limitadas, 25% exento, extra-límite y RLG general

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Motor — cédula de pensiones

**Files:**
- Create: `src/declaras/motor/pensiones.py`
- Test: `tests/test_pensiones.py`

**Interfaces:**
- Produces: `rlg_pensiones(caso, p, t) -> int` — registra `RLG_PENSIONES`. Exención de `pension_exenta_uvt_mes` (1.000 UVT) **por mes**: gravado = Σ max(0, mesada_m − 1.000 UVT).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_pensiones.py
from declaras.caso import CasoTributario, Contribuyente, Fuente, IngresoPension
from declaras.motor.pensiones import rlg_pensiones
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(mesadas):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="2", nombre="G2"),
        pensiones=[IngresoPension(pagador="Colpensiones", mesadas=mesadas, fuente=FX)],
    )


def test_mesada_bajo_tope_exenta_total():
    assert rlg_pensiones(_caso([10_000_000] * 12), P, Traza()) == 0


def test_mesada_sobre_tope_grava_exceso_mensual():
    # 55M/mes: exceso (55.000.000 − 49.799.000) × 12 = 62.412.000
    assert rlg_pensiones(_caso([55_000_000] * 12), P, Traza()) == 62_412_000


def test_mesadas_variables_mes_a_mes():
    # solo los meses que exceden 1.000 UVT gravan: 60M excede en 10.201.000
    mesadas = [40_000_000] * 11 + [60_000_000]
    assert rlg_pensiones(_caso(mesadas), P, Traza()) == 10_201_000


def test_sin_pensiones():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="0", nombre="Z"))
    assert rlg_pensiones(caso, P, Traza()) == 0
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_pensiones.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/motor/pensiones.py
from declaras.caso import CasoTributario
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def rlg_pensiones(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> int:
    """Cédula de pensiones: exención de 1.000 UVT POR MES; el exceso grava."""
    tope_mes = p.uvt_pesos(p.pension_exenta_uvt_mes)
    gravado = sum(
        max(0, mesada - tope_mes)
        for pension in caso.pensiones
        for mesada in pension.mesadas
    )
    return t.nodo(
        "RLG_PENSIONES", "Renta líquida gravable cédula de pensiones",
        gravado,
        f"Σ max(0, mesada_mes − 1.000 UVT = {tope_mes:,}) sobre 12 meses",
        regla="art. 206 num. 5 ET",
    )
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_pensiones.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor/pensiones.py tests/test_pensiones.py
git commit -m "Motor: cédula de pensiones con exención mensual de 1.000 UVT

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Motor — dividendos, tabla 241 y descuentos

**Files:**
- Create: `src/declaras/motor/impuesto.py`
- Test: `tests/test_impuesto.py`

**Interfaces:**
- Consumes: `impuesto_tabla_241`.
- Produces: `impuesto_total(caso, p, t, rlg_general: int, rlg_pensiones: int) -> int` — registra `DIV_NO_GRAVADOS, DIV_GRAVADOS, IMP_DIV_35, BASE_TABLA_241, IMPUESTO_241, DESCUENTO_254_1, DESCUENTO_DONACIONES, IMPUESTO_NETO`; devuelve `IMPUESTO_NETO`.
- Reglas: gravados → 35% primero, el neto va a la tabla; no gravados → directo a la tabla; descuento 254-1 = 19% × max(0, (no_gravados + neto_gravados) − 1.090 UVT); donaciones certificadas → descuento 25%; `IMPUESTO_NETO = max(0, IMPUESTO_241 + IMP_DIV_35 − descuentos)`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_impuesto.py
from declaras.caso import CasoTributario, Contribuyente, Dividendo, Fuente
from declaras.motor.impuesto import impuesto_total
from declaras.motor.traza import Traza
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso(dividendos=()):
    return CasoTributario(contribuyente=Contribuyente(num_doc="3", nombre="G3"),
                          dividendos=list(dividendos))


def test_sin_dividendos_solo_tabla():
    t = Traza()
    assert impuesto_total(_caso(), P, t, rlg_general=62_154_472, rlg_pensiones=0) \
        == 1_495_977
    assert t.nodos["DESCUENTO_254_1"].valor == 0


def test_dividendos_mixtos_g3():
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA",
                    no_gravados=30_000_000, gravados=10_000_000, fuente=FX)
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=82_478_944, rlg_pensiones=0)
    assert t.nodos["IMP_DIV_35"].valor == 3_500_000
    assert t.nodos["BASE_TABLA_241"].valor == 118_978_944  # 82.478.944 + 30M + 6.5M
    assert t.nodos["IMPUESTO_241"].valor == 15_386_464     # 28% + 116 UVT
    assert t.nodos["DESCUENTO_254_1"].valor == 0           # 36.5M < 1.090 UVT
    assert v == 18_886_464


def test_descuento_254_1_sobre_umbral():
    div = Dividendo(sociedad_nit="800", sociedad_nombre="Soc SA",
                    no_gravados=80_000_000, fuente=FX)
    t = Traza()
    v = impuesto_total(_caso([div]), P, t, rlg_general=50_000_000, rlg_pensiones=0)
    # base 130M → imp241 18.472.360 (28% + 116 UVT); descuento 19% × (80M − 54.280.910)
    assert t.nodos["IMPUESTO_241"].valor == 18_472_360
    assert t.nodos["DESCUENTO_254_1"].valor == 4_886_627
    assert v == 13_585_733
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_impuesto.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/motor/impuesto.py
from declaras.caso import CasoTributario
from declaras.dinero import pesos
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio
from declaras.parametros.tabla import impuesto_tabla_241


def impuesto_total(caso: CasoTributario, p: ParametrosAnio, t: Traza,
                   rlg_general: int, rlg_pensiones: int) -> int:
    """Cédula de dividendos + tabla 241 + descuentos → impuesto neto."""
    no_grav = t.nodo("DIV_NO_GRAVADOS", "Dividendos no gravados (art. 49)",
                     sum(d.no_gravados for d in caso.dividendos),
                     "suma certificados", regla="art. 242 ET")
    grav = t.nodo("DIV_GRAVADOS", "Dividendos gravados",
                  sum(d.gravados for d in caso.dividendos),
                  "suma certificados", regla="art. 240 ET")

    imp_35 = t.nodo("IMP_DIV_35", "Impuesto 35% sobre dividendos gravados",
                    pesos(grav * p.dividendos_tarifa_gravados),
                    f"35% × {grav:,}", insumos=["DIV_GRAVADOS"],
                    regla="art. 242 par. / art. 240 ET")
    neto_grav = grav - imp_35

    base = t.nodo("BASE_TABLA_241", "Base gravable tabla art. 241",
                  rlg_general + rlg_pensiones + no_grav + neto_grav,
                  f"RLG_GENERAL {rlg_general:,} + RLG_PENSIONES {rlg_pensiones:,} "
                  f"+ no gravados {no_grav:,} + neto gravados {neto_grav:,}",
                  insumos=["RLG_GENERAL", "RLG_PENSIONES", "DIV_NO_GRAVADOS", "DIV_GRAVADOS"])

    imp_241 = t.nodo("IMPUESTO_241", "Impuesto tabla art. 241",
                     impuesto_tabla_241(base, p), "tabla marginal art. 241",
                     insumos=["BASE_TABLA_241"], regla="art. 241 ET")

    base_desc = max(0, (no_grav + neto_grav)
                    - p.uvt_pesos(p.descuento_dividendos_umbral_uvt))
    desc_div = t.nodo("DESCUENTO_254_1", "Descuento marginal por dividendos",
                      pesos(base_desc * p.descuento_dividendos_pct),
                      f"19% × max(0, dividendos en base − 1.090 UVT) = 19% × {base_desc:,}",
                      regla="art. 254-1 ET")

    donado = sum(d.valor for d in caso.beneficios.donaciones_esal if d.certificada)
    desc_don = t.nodo("DESCUENTO_DONACIONES", "Descuento donaciones ESAL certificadas",
                      pesos(donado * p.donaciones_descuento_pct),
                      f"25% × {donado:,}", regla="art. 257 ET")

    return t.nodo("IMPUESTO_NETO", "Impuesto neto de renta",
                  max(0, imp_241 + imp_35 - desc_div - desc_don),
                  f"{imp_241:,} + {imp_35:,} − {desc_div:,} − {desc_don:,} (piso 0)",
                  insumos=["IMPUESTO_241", "IMP_DIV_35",
                           "DESCUENTO_254_1", "DESCUENTO_DONACIONES"])
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_impuesto.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor/impuesto.py tests/test_impuesto.py
git commit -m "Motor: dividendos (35% + tabla), descuento 254-1 y donaciones

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Motor — cierre, validaciones y `liquidar()`

**Files:**
- Create: `src/declaras/motor/cierre.py`, `src/declaras/motor/liquidar.py`
- Modify: `src/declaras/motor/__init__.py` (exportar `liquidar`, `Elecciones`, `Liquidacion`)
- Test: `tests/test_cierre.py`

**Interfaces:**
- Produces:
  - `cerrar(caso, p, t, impuesto_neto: int) -> None` — registra `RETENCIONES` (suma de todas las fuentes), `ANTICIPO_SIGUIENTE` (pct por `anios_previos_declarando`: 0→25%, 1→50%, ≥2→75%; base = `impuesto_neto` o, si `impuesto_neto_anio_anterior` no es None y años≥1, `min(impuesto_neto, promedio de ambos)`; anticipo = `max(0, pesos(base×pct) − retenciones)`) y `SALDO = impuesto_neto + anticipo − retenciones − anticipo_pagado − saldo_favor_anterior` (positivo = a pagar, negativo = a favor).
  - `validar(caso, p, t) -> None` — registra `PATRIMONIO_BRUTO`, `PATRIMONIO_LIQUIDO`, `OBLIGADO_DECLARAR` (1/0, con los criterios que dispararon en la fórmula; si 0 → flag `NO_OBLIGADO` severidad info) y, si hay `patrimonio_liquido_anterior`, el chequeo de **comparación patrimonial**: si `RLG_GENERAL + RLG_PENSIONES + DIV en base + APLICADO_40 + EXTRA_LIMITE + INCR_TOTAL < (incremento patrimonial + retenciones + anticipo_pagado)` → flag `COMPARACION_PATRIMONIAL` (aproximación del art. 236-239 ET para revisión del contador, no ajusta la base).
  - `liquidar(caso, p, elecciones) -> Liquidacion` — orquesta: `base_general → rlg_general → rlg_pensiones → impuesto_total → cerrar → validar → t.a_liquidacion(...)`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_cierre.py
from declaras.caso import (
    Activo, CasoTributario, Contribuyente, Creditos, Deuda, Fuente,
    IngresoLaboral, Patrimonio,
)
from declaras.motor import Elecciones, liquidar
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso_laboral(**creditos_kw):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="X"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
            aportes_salud=4_800_000, aportes_pension=4_800_000,
            retencion=8_000_000, fuente=FX)],
        creditos=Creditos(**creditos_kw),
    )


def test_saldo_a_favor_primer_anio():
    liq = liquidar(_caso_laboral(anios_previos_declarando=0), P,
                   Elecciones(usar_387=False, usar_72uvt=False))
    # sin beneficios: 25% = min(25%×110.4M, 790 UVT) = 27.600.000 ≤ cap 44.16M
    # RLG = 110.4M − 27.6M = 82.800.000 → imp241 = 28.519.090×0.19 = 5.418.627
    assert liq.valor("IMPUESTO_NETO") == 5_418_627
    assert liq.valor("RETENCIONES") == 8_000_000
    # anticipo 25% × 5.418.627 = 1.354.657 − 8M → 0
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == 5_418_627 - 8_000_000  # a favor


def test_anticipo_promedio_dos_anios():
    liq = liquidar(_caso_laboral(anios_previos_declarando=2,
                                 impuesto_neto_anio_anterior=1_000_000), P,
                   Elecciones(usar_387=False, usar_72uvt=False))
    imp = liq.valor("IMPUESTO_NETO")           # 5.418.627
    promedio = round((imp + 1_000_000) / 2)    # 3.209.314 (menor que imp)
    esperado = max(0, round(promedio * 0.75) - 8_000_000)
    assert liq.valor("ANTICIPO_SIGUIENTE") == esperado == 0


def test_obligado_por_patrimonio_y_comparacion():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="0", nombre="G0"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT",
                            valor_31dic=250_000_000, fuente=FX)],
            deudas=[], patrimonio_liquido_anterior=200_000_000),
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 1        # patrimonio > 4.500 UVT
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.tiene_flag("COMPARACION_PATRIMONIAL")  # creció 50M sin rentas


def test_no_obligado():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="9", nombre="Z"))
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 0
    assert liq.tiene_flag("NO_OBLIGADO")
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_cierre.py -q`
Expected: FAIL.

Verificación a mano del primer test: base25 = 120M−9.6M−0 = 110.4M → 25% = 27.600.000 (< 790 UVT = 39.341.210 ✓, < cap 44.160.000 ✓). RLG = 110.400.000 − 27.600.000 = 82.800.000. Impuesto: (82.800.000 − 54.280.910) × 0,19 = 28.519.090 × 0,19 = 5.418.627,1 → **5.418.627**.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/motor/cierre.py
from declaras.caso import CasoTributario
from declaras.dinero import pesos
from declaras.motor.traza import Traza
from declaras.parametros import ParametrosAnio


def cerrar(caso: CasoTributario, p: ParametrosAnio, t: Traza, impuesto_neto: int) -> None:
    retenciones = t.nodo(
        "RETENCIONES", "Total retenciones en la fuente",
        sum(l.retencion for l in caso.laborales)
        + sum(pn.retencion for pn in caso.pensiones)
        + sum(r.retencion for r in caso.rendimientos)
        + sum(a.retencion for a in caso.arriendos)
        + sum(d.retencion for d in caso.dividendos),
        "suma de retenciones de todas las fuentes",
    )

    anios = caso.creditos.anios_previos_declarando
    pct = p.anticipo_pct[min(anios, len(p.anticipo_pct) - 1)]
    base = impuesto_neto
    detalle = f"impuesto del año {impuesto_neto:,}"
    anterior = caso.creditos.impuesto_neto_anio_anterior
    if anios >= 1 and anterior is not None:
        promedio = pesos((impuesto_neto + anterior) / 2)
        if promedio < base:
            base, detalle = promedio, f"promedio dos años {promedio:,} (menor)"
    anticipo = t.nodo(
        "ANTICIPO_SIGUIENTE", "Anticipo del año siguiente",
        max(0, pesos(base * pct) - retenciones),
        f"max(0, {pct:.0%} × {detalle} − retenciones {retenciones:,})",
        insumos=["IMPUESTO_NETO", "RETENCIONES"], regla="art. 807 ET",
    )

    t.nodo(
        "SALDO", "Saldo a pagar (+) o a favor (−)",
        impuesto_neto + anticipo - retenciones
        - caso.creditos.anticipo_pagado - caso.creditos.saldo_favor_anterior,
        "IMPUESTO_NETO + ANTICIPO_SIGUIENTE − RETENCIONES − anticipo pagado − saldo a favor anterior",
        insumos=["IMPUESTO_NETO", "ANTICIPO_SIGUIENTE", "RETENCIONES"],
    )


def validar(caso: CasoTributario, p: ParametrosAnio, t: Traza) -> None:
    bruto_pat = t.nodo("PATRIMONIO_BRUTO", "Patrimonio bruto a 31 dic",
                       sum(a.valor_31dic for a in caso.patrimonio.activos),
                       "suma de activos")
    liquido = t.nodo("PATRIMONIO_LIQUIDO", "Patrimonio líquido a 31 dic",
                     bruto_pat - sum(d.saldo_31dic for d in caso.patrimonio.deudas),
                     "PATRIMONIO_BRUTO − deudas", insumos=["PATRIMONIO_BRUTO"])

    criterios = []
    if caso.ingresos_brutos_totales >= p.uvt_pesos(p.tope_obligacion_ingresos_uvt):
        criterios.append("ingresos ≥ 1.400 UVT")
    if bruto_pat > p.uvt_pesos(p.tope_obligacion_patrimonio_uvt):
        criterios.append("patrimonio > 4.500 UVT")
    mov = caso.movimientos
    if mov.consignaciones_totales and mov.consignaciones_totales.valor \
            > p.uvt_pesos(p.tope_obligacion_consignaciones_uvt):
        criterios.append("consignaciones > 1.400 UVT")
    if mov.compras_y_consumos and mov.compras_y_consumos.valor \
            > p.uvt_pesos(p.tope_obligacion_consignaciones_uvt):
        criterios.append("compras y consumos > 1.400 UVT")
    t.nodo("OBLIGADO_DECLARAR", "¿Obligado a declarar?",
           1 if criterios else 0,
           "; ".join(criterios) or "ningún criterio superado",
           regla="art. 592-594-3 ET")
    if not criterios:
        t.flag("NO_OBLIGADO", "No supera ningún tope de obligación: la declaración "
               "sería voluntaria.", severidad="info")

    anterior = caso.patrimonio.patrimonio_liquido_anterior
    if anterior is not None:
        incremento = liquido - anterior
        justificado = (
            t.nodos["RLG_GENERAL"].valor + t.nodos["RLG_PENSIONES"].valor
            + t.nodos["DIV_NO_GRAVADOS"].valor + t.nodos["DIV_GRAVADOS"].valor
            + t.nodos["APLICADO_40"].valor + t.nodos["EXTRA_LIMITE"].valor
            + t.nodos["INCR_TOTAL"].valor
        )
        gastado = (t.nodos["RETENCIONES"].valor + caso.creditos.anticipo_pagado)
        if justificado < incremento + gastado:
            t.flag(
                "COMPARACION_PATRIMONIAL",
                f"El patrimonio líquido creció {incremento:,} pero las rentas del año "
                f"solo justifican {justificado:,} (aprox.). Documentar el origen "
                "(herencia, donación, préstamo, venta) antes de presentar.",
            )
```

```python
# src/declaras/motor/liquidar.py
from declaras.caso import CasoTributario
from declaras.motor.cierre import cerrar, validar
from declaras.motor.elecciones import Elecciones
from declaras.motor.general import base_general, rlg_general
from declaras.motor.impuesto import impuesto_total
from declaras.motor.pensiones import rlg_pensiones
from declaras.motor.traza import Liquidacion, Traza
from declaras.parametros import ParametrosAnio


def liquidar(caso: CasoTributario, p: ParametrosAnio,
             elecciones: Elecciones) -> Liquidacion:
    """Función pura: Caso + Parámetros + Elecciones → Liquidación trazable."""
    t = Traza()
    base_general(caso, p, t)
    rg = rlg_general(caso, p, elecciones, t)
    rp = rlg_pensiones(caso, p, t)
    imp = impuesto_total(caso, p, t, rg, rp)
    cerrar(caso, p, t, imp)
    validar(caso, p, t)
    return t.a_liquidacion(caso.anio_gravable, elecciones)
```

```python
# src/declaras/motor/__init__.py
from declaras.motor.elecciones import Elecciones
from declaras.motor.liquidar import liquidar
from declaras.motor.traza import Flag, Liquidacion, Nodo

__all__ = ["Elecciones", "Flag", "Liquidacion", "Nodo", "liquidar"]
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_cierre.py -q && uv run pytest -q`
Expected: todos verdes.

- [ ] **Step 5: Commit**

```bash
git add src/declaras/motor tests/test_cierre.py
git commit -m "Motor: cierre (retenciones, anticipo, saldo), validaciones y liquidar()

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Optimizador

**Files:**
- Create: `src/declaras/optimizador/__init__.py`
- Test: `tests/test_optimizador.py`

**Interfaces:**
- Consumes: `liquidar`, `Elecciones`, `Liquidacion`.
- Produces: `ResultadoOptimizacion{liquidacion: Liquidacion, elecciones: Elecciones, evaluadas: int}`; `optimizar(caso, p) -> ResultadoOptimizacion`; `ahorro_marginal(caso_base, caso_con_hecho, p) -> int` (= impuesto óptimo del base − impuesto óptimo con el hecho).
- Desempate determinista (a igual impuesto): (1) menos elecciones activas, (2) orden lexicográfico de la tupla `(usar_387, usar_72uvt)` con False < True.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_optimizador.py
from declaras.motor import Elecciones, liquidar
from declaras.optimizador import ahorro_marginal, optimizar
from declaras.parametros import cargar
from tests.test_rlg_general import caso_g1, caso_g3_parcial

P = cargar(2025)


def test_g1_desempate_prefiere_menos_elecciones():
    r = optimizar(caso_g1(), P)
    # cap copado: (F,T) y (T,T) empatan en impuesto → gana (F,T)
    assert r.elecciones == Elecciones(usar_387=False, usar_72uvt=True)
    assert r.liquidacion.valor("IMPUESTO_NETO") == 1_495_977
    assert r.evaluadas == 4


def test_g3_elige_387_y_72uvt():
    r = optimizar(caso_g3_parcial(), P)
    assert r.elecciones == Elecciones(usar_387=True, usar_72uvt=True)


def test_nunca_peor_que_ingenuo():
    for caso in (caso_g1(), caso_g3_parcial()):
        opt = optimizar(caso, P).liquidacion.valor("IMPUESTO_NETO")
        ingenuo = liquidar(caso, P, Elecciones(usar_387=False,
                                               usar_72uvt=False)).valor("IMPUESTO_NETO")
        assert opt <= ingenuo


def test_ahorro_marginal_de_un_dependiente():
    con = caso_g3_parcial()
    sin = con.model_copy(deep=True)
    sin.beneficios.dependientes = sin.beneficios.dependientes[:1]  # quita 1 de 2
    ahorro = ahorro_marginal(sin, con, P)
    assert ahorro > 0  # un dependiente extra ahorra impuesto real


def test_sin_dependientes_un_solo_combo():
    caso = caso_g1()
    caso.beneficios.dependientes = []
    assert optimizar(caso, P).evaluadas == 1
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_optimizador.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/optimizador/__init__.py
from pydantic import BaseModel

from declaras.caso import CasoTributario
from declaras.motor import Elecciones, Liquidacion, liquidar
from declaras.parametros import ParametrosAnio

__all__ = ["ResultadoOptimizacion", "optimizar", "ahorro_marginal"]


class ResultadoOptimizacion(BaseModel):
    liquidacion: Liquidacion
    elecciones: Elecciones
    evaluadas: int


def _combos(caso: CasoTributario) -> list[Elecciones]:
    if not caso.beneficios.dependientes:
        return [Elecciones(usar_387=False, usar_72uvt=False)]
    return [Elecciones(usar_387=a, usar_72uvt=b)
            for a in (False, True) for b in (False, True)]


def optimizar(caso: CasoTributario, p: ParametrosAnio) -> ResultadoOptimizacion:
    """Enumera las elecciones legales, evalúa el motor y elige la de menor impuesto.

    Desempate determinista: menos elecciones activas, luego orden de la tupla.
    """
    evaluados = [(liquidar(caso, p, e), e) for e in _combos(caso)]
    liq, e = min(
        evaluados,
        key=lambda par: (par[0].valor("IMPUESTO_NETO"), par[1].activas,
                         (par[1].usar_387, par[1].usar_72uvt)),
    )
    return ResultadoOptimizacion(liquidacion=liq, elecciones=e,
                                 evaluadas=len(evaluados))


def ahorro_marginal(caso_base: CasoTributario, caso_con_hecho: CasoTributario,
                    p: ParametrosAnio) -> int:
    """Cuánto impuesto ahorra un hecho: base del 'cada pregunta lleva su ahorro'."""
    return (optimizar(caso_base, p).liquidacion.valor("IMPUESTO_NETO")
            - optimizar(caso_con_hecho, p).liquidacion.valor("IMPUESTO_NETO"))
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_optimizador.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/optimizador tests/test_optimizador.py
git commit -m "Optimizador: enumeración exhaustiva con desempate determinista y ahorro marginal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Golden cases G0–G3

**Files:**
- Create: `tests/golden/__init__.py`, `tests/golden/casos.py`, `tests/golden/test_golden.py`

**Interfaces:**
- Consumes: todo el motor + optimizador.
- Produces: `casos.py` con constructores `g0(), g1(), g2(), g3()` reutilizables por el API y el front. Los valores esperados de estos tests son el **contrato del motor** — cambiarlos exige justificación normativa.

- [ ] **Step 1: Escribir fixtures y tests (fallan si el motor tiene errores de integración)**

```python
# tests/golden/casos.py
"""Golden cases sintéticos, verificables a mano. Un caso por escenario del spec."""
from declaras.caso import (
    Activo, Arriendo, Beneficios, CasoTributario, Contribuyente, CostosArriendo,
    Creditos, Dependiente, Deuda, Dividendo, Fuente, IngresoLaboral,
    IngresoPension, MontoDeclarado, Movimientos, Patrimonio, Rendimiento,
)

FX = Fuente.fixture("golden")


def _md(v: int) -> MontoDeclarado:
    return MontoDeclarado(valor=v, fuente=FX)


def g0() -> CasoTributario:
    """Fácil sin movimientos: obligado solo por patrimonio, impuesto 0."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="10", nombre="G0 Fácil"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT",
                            valor_31dic=250_000_000, fuente=FX)],
            patrimonio_liquido_anterior=250_000_000),
    )


def g1() -> CasoTributario:
    """Asalariado con beneficios: el límite del 40% se copa."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="11", nombre="G1 Asalariado"),
        laborales=[IngresoLaboral(
            empleador_nit="900111222", empleador_nombre="ACME SAS",
            salarios=120_000_000, aportes_salud=4_800_000,
            aportes_pension=4_800_000, retencion=8_000_000, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX)],
            medicina_prepagada=_md(6_000_000),
            intereses_vivienda=_md(18_000_000),
            gmf_pagado=_md(1_000_000),
            facturas_electronicas_total=_md(50_000_000)),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="inmueble", descripcion="Apto",
                            valor_31dic=300_000_000, fuente=FX),
                     Activo(tipo="cuenta", descripcion="Ahorros",
                            valor_31dic=20_000_000, fuente=FX)],
            deudas=[Deuda(acreedor="Banco", saldo_31dic=150_000_000, fuente=FX)],
            patrimonio_liquido_anterior=165_000_000),
        creditos=Creditos(anios_previos_declarando=0),
    )


def g2() -> CasoTributario:
    """Asalariado + pensión alta + movimientos (rendimientos, GMF, consignaciones)."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="12", nombre="G2 Pensionado"),
        laborales=[IngresoLaboral(
            empleador_nit="900333444", empleador_nombre="Universidad X",
            salarios=80_000_000, aportes_salud=3_200_000,
            aportes_pension=3_200_000, retencion=3_000_000, fuente=FX)],
        pensiones=[IngresoPension(pagador="Colpensiones",
                                  mesadas=[55_000_000] * 12, fuente=FX)],
        rendimientos=[Rendimiento(entidad="Banco Y", valor=8_000_000,
                                  retencion=560_000, fuente=FX)],
        beneficios=Beneficios(gmf_pagado=_md(800_000)),
        movimientos=Movimientos(consignaciones_totales=_md(700_000_000)),
        creditos=Creditos(anios_previos_declarando=2),
    )


def g3() -> CasoTributario:
    """Asalariado + arriendos con costos + dividendos mixtos."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="13", nombre="G3 Capital"),
        laborales=[IngresoLaboral(
            empleador_nit="900555666", empleador_nombre="Consultora Z",
            salarios=100_000_000, aportes_salud=4_000_000,
            aportes_pension=4_000_000, retencion=6_000_000, fuente=FX)],
        arriendos=[Arriendo(
            inmueble="Apto arrendado", canon_total=36_000_000, retencion=1_260_000,
            costos=CostosArriendo(predial=3_000_000, administracion=4_800_000,
                                  comision_inmobiliaria=3_600_000), fuente=FX)],
        dividendos=[Dividendo(sociedad_nit="800777888", sociedad_nombre="Soc SA",
                              no_gravados=30_000_000, gravados=10_000_000,
                              retencion=0, fuente=FX)],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", fuente=FX),
                          Dependiente(tipo="hijo_estudiante", fuente=FX)],
            gmf_pagado=_md(900_000)),
        creditos=Creditos(anios_previos_declarando=2),
    )
```

```python
# tests/golden/test_golden.py
"""Contrato del motor: 210 esperado por escenario, calculado a mano en el plan."""
from declaras.motor import Elecciones
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from tests.golden.casos import g0, g1, g2, g3

P = cargar(2025)


def test_g0_facil_sin_movimientos():
    liq = optimizar(g0(), P).liquidacion
    assert liq.valor("OBLIGADO_DECLARAR") == 1     # patrimonio > 224.095.500
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.valor("SALDO") == 0
    assert not liq.tiene_flag("COMPARACION_PATRIMONIAL")  # incremento 0


def test_g1_asalariado():
    r = optimizar(g1(), P)
    liq = r.liquidacion
    assert r.elecciones == Elecciones(usar_387=False, usar_72uvt=True)
    assert liq.valor("RLG_GENERAL") == 62_154_472
    assert liq.valor("IMPUESTO_NETO") == 1_495_977
    assert liq.valor("RETENCIONES") == 8_000_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == -6_504_023        # a favor
    assert not liq.tiene_flag("COMPARACION_PATRIMONIAL")  # creció 5M, justificado


def test_g2_pension_y_movimientos():
    liq = optimizar(g2(), P).liquidacion
    assert liq.valor("RLG_GENERAL") == 62_800_000
    assert liq.valor("RLG_PENSIONES") == 62_412_000        # exceso mensual × 12
    assert liq.valor("IMPUESTO_NETO") == 17_131_720        # 28% + 116 UVT
    assert liq.valor("RETENCIONES") == 3_560_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 9_288_790    # 75% − retenciones
    assert liq.valor("SALDO") == 22_860_510
    assert liq.valor("OBLIGADO_DECLARAR") == 1             # también por consignaciones
    assert liq.tiene_flag("COMPONENTE_INFLACIONARIO_PROVISIONAL")


def test_g3_capital_y_dividendos():
    r = optimizar(g3(), P)
    liq = r.liquidacion
    assert r.elecciones == Elecciones(usar_387=True, usar_72uvt=True)
    assert liq.valor("RLG_GENERAL") == 82_478_944
    assert liq.valor("IMP_DIV_35") == 3_500_000
    assert liq.valor("DESCUENTO_254_1") == 0
    assert liq.valor("IMPUESTO_NETO") == 18_886_464        # 15.386.464 + 3.5M
    assert liq.valor("RETENCIONES") == 7_540_000
    assert liq.valor("ANTICIPO_SIGUIENTE") == 6_624_848
    assert liq.valor("SALDO") == 17_971_312
```

- [ ] **Step 2: Correr**

Run: `uv run pytest tests/golden -q`
Expected: `4 passed`. Si algo falla, el error está en el motor (los esperados están verificados a mano en este plan) — depurar el motor, no ajustar el esperado.

Memoria de cálculo de los esperados (para el implementador y el contador):
- **G1**: netos 110.4M; cap = 40% = 44.16M; limitadas+25% > cap → aplicado 44.16M; extra = 72 UVT (3.585.528) + 1% de 50M (500.000); RLG = 110.4M − 44.16M − 4.085.528 = 62.154.472; imp = (62.154.472 − 54.280.910)×19% = 1.495.977; anticipo 25%×imp − 8M < 0 → 0; saldo = 1.495.977 − 8.000.000 = −6.504.023.
- **G2**: netos 81.6M (bruto 88M − aportes 6.4M, CI provisional 0); cap 32.64M; limitadas = GMF 400.000 + 25% de (80M−6.4M)=18.4M → 18.8M ≤ cap; RLG gen = 62.8M; pensiones = (55M−49.799M)×12 = 62.412.000; base 125.212.000 → imp = (base − 1.700 UVT)×28% + 116 UVT = 11.355.036 + 5.776.684 = 17.131.720; anticipo = 75%×imp − 3.56M = 9.288.790; saldo = 17.131.720 − 3.560.000 + 9.288.790 = 22.860.510.
- **G3** (con 387): netos 132M; costos 11.4M; limitadas = GMF 450.000 + 387 10M + 25% de (100M−8M−10M)=20.5M → 30.95M ≤ cap 52.8M; extra 72×2 UVT = 7.171.056; RLG = 82.478.944; dividendos: 35%×10M=3.5M, base = 82.478.944+30M+6.5M = 118.978.944 → imp241 = (base − 1.700 UVT)×28% + 116 UVT = 15.386.464; descuento 254-1: 36.5M < 54.280.910 → 0; imp neto = 18.886.464; anticipo = 75% − 7.54M = 6.624.848; saldo = 17.971.312.

- [ ] **Step 3: Commit**

```bash
git add tests/golden
git commit -m "Golden cases G0-G3: contrato del motor con 210 esperado a mano

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Render — casillas, memoria de cálculo y borrador HTML

**Files:**
- Create: `src/declaras/render/__init__.py`, `src/declaras/render/orden.py`, `src/declaras/render/memoria.py`, `src/declaras/render/html.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Liquidacion`, `CasoTributario`.
- Produces: `ORDEN_CASILLAS: list[str]` (los códigos de nodo en orden de presentación); `casillas(liq) -> list[dict]` (dicts `{codigo, etiqueta, valor, formula, regla}` en ese orden, solo nodos presentes); `memoria_markdown(liq, caso) -> str`; `borrador_html(liq, caso) -> str` (página imprimible, autocontenida, sin assets externos).
- Nota: el mapeo a números de casilla oficiales del 210 es el pendiente #2 del spec; el render usa los códigos de concepto.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_render.py
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render import borrador_html, casillas, memoria_markdown
from tests.golden.casos import g1

P = cargar(2025)


def _liq():
    return optimizar(g1(), P).liquidacion


def test_casillas_ordenadas_y_completas():
    filas = casillas(_liq())
    codigos = [f["codigo"] for f in filas]
    assert codigos.index("ING_BRUTO_GENERAL") < codigos.index("RLG_GENERAL") \
        < codigos.index("IMPUESTO_NETO") < codigos.index("SALDO")
    saldo = next(f for f in filas if f["codigo"] == "SALDO")
    assert saldo["valor"] == -6_504_023


def test_memoria_incluye_formulas_y_flags():
    md = memoria_markdown(_liq(), g1())
    assert "RLG_GENERAL" in md and "62,154,472" in md
    assert "min(" in md  # las fórmulas viajan
    assert "G1 Asalariado" in md


def test_html_imprimible():
    html = borrador_html(_liq(), g1())
    assert "<table" in html and "IMPUESTO_NETO" in html
    assert "http://" not in html and "https://" not in html  # autocontenido
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_render.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/render/orden.py
ORDEN_CASILLAS: list[str] = [
    "OBLIGADO_DECLARAR",
    "PATRIMONIO_BRUTO", "PATRIMONIO_LIQUIDO",
    "ING_BRUTO_GENERAL", "INCR_APORTES", "INCR_CI", "INCR_TOTAL",
    "ING_NETOS_GENERAL", "COSTOS_ARRIENDOS", "CAP_40",
    "DEDUCCIONES_LIMITADAS", "EXENTA_25", "APLICADO_40", "EXTRA_LIMITE",
    "RLG_GENERAL", "RLG_PENSIONES",
    "DIV_NO_GRAVADOS", "DIV_GRAVADOS", "IMP_DIV_35",
    "BASE_TABLA_241", "IMPUESTO_241", "DESCUENTO_254_1", "DESCUENTO_DONACIONES",
    "IMPUESTO_NETO", "RETENCIONES", "ANTICIPO_SIGUIENTE", "SALDO",
]
```

```python
# src/declaras/render/memoria.py
from declaras.caso import CasoTributario
from declaras.motor import Liquidacion
from declaras.render.orden import ORDEN_CASILLAS


def casillas(liq: Liquidacion) -> list[dict]:
    filas = []
    for codigo in ORDEN_CASILLAS:
        if codigo in liq.nodos:
            n = liq.nodos[codigo]
            filas.append({"codigo": n.codigo, "etiqueta": n.etiqueta,
                          "valor": n.valor, "formula": n.formula, "regla": n.regla})
    return filas


def memoria_markdown(liq: Liquidacion, caso: CasoTributario) -> str:
    lineas = [
        f"# Memoria de cálculo — {caso.contribuyente.nombre} "
        f"({caso.contribuyente.tipo_doc} {caso.contribuyente.num_doc})",
        f"Año gravable {liq.anio_gravable} · elecciones: "
        f"art387={'sí' if liq.elecciones.usar_387 else 'no'}, "
        f"72UVT={'sí' if liq.elecciones.usar_72uvt else 'no'}",
        "",
    ]
    for f in casillas(liq):
        regla = f" _({f['regla']})_" if f["regla"] else ""
        lineas.append(f"## {f['codigo']} — {f['etiqueta']}{regla}")
        lineas.append(f"**Valor:** {f['valor']:,}")
        lineas.append(f"**Cómo:** {f['formula']}")
        lineas.append("")
    if liq.flags:
        lineas.append("## Alertas")
        for fl in liq.flags:
            lineas.append(f"- **[{fl.severidad}] {fl.codigo}**: {fl.mensaje}")
    return "\n".join(lineas)
```

```python
# src/declaras/render/html.py
from jinja2 import Template

from declaras.caso import CasoTributario
from declaras.motor import Liquidacion
from declaras.render.memoria import casillas

_PLANTILLA = Template("""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Borrador 210 — {{ caso.contribuyente.nombre }}</title>
<style>
body{font-family:Georgia,serif;max-width:900px;margin:2rem auto;color:#1a1a1a}
h1{font-size:1.4rem} table{border-collapse:collapse;width:100%}
td,th{border:1px solid #bbb;padding:.45rem .6rem;font-size:.9rem;text-align:left}
td.v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.neg td.v{color:#0a6e0a} .flag{background:#fff3cd;border:1px solid #e0c000;
padding:.5rem .8rem;margin:.4rem 0;font-size:.9rem}
small{color:#555} @media print{.flag{break-inside:avoid}}
</style></head><body>
<h1>Borrador Formulario 210 — año gravable {{ liq.anio_gravable }}</h1>
<p>{{ caso.contribuyente.nombre }} · {{ caso.contribuyente.tipo_doc }}
{{ caso.contribuyente.num_doc }}<br>
<small>BORRADOR por conceptos — el mapeo a casillas oficiales DIAN está pendiente.
Elecciones: art. 387 = {{ "sí" if liq.elecciones.usar_387 else "no" }},
72 UVT = {{ "sí" if liq.elecciones.usar_72uvt else "no" }}.</small></p>
{% for f in liq.flags %}<div class="flag"><b>{{ f.codigo }}</b> — {{ f.mensaje }}</div>{% endfor %}
<table><tr><th>Concepto</th><th>Valor</th><th>Cómo se calculó</th><th>Norma</th></tr>
{% for c in filas %}<tr{% if c.valor < 0 %} class="neg"{% endif %}>
<td><b>{{ c.etiqueta }}</b><br><small>{{ c.codigo }}</small></td>
<td class="v">{{ "{:,}".format(c.valor) }}</td>
<td><small>{{ c.formula }}</small></td><td><small>{{ c.regla or "" }}</small></td>
</tr>{% endfor %}</table>
</body></html>""")


def borrador_html(liq: Liquidacion, caso: CasoTributario) -> str:
    return _PLANTILLA.render(liq=liq, caso=caso, filas=casillas(liq))
```

```python
# src/declaras/render/__init__.py
from declaras.render.html import borrador_html
from declaras.render.memoria import casillas, memoria_markdown
from declaras.render.orden import ORDEN_CASILLAS

__all__ = ["ORDEN_CASILLAS", "borrador_html", "casillas", "memoria_markdown"]
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_render.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/render tests/test_render.py
git commit -m "Render: casillas ordenadas, memoria de cálculo y borrador HTML imprimible

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Extractor del 220 (LLM)

**Files:**
- Create: `src/declaras/extraccion/__init__.py`, `src/declaras/extraccion/f220.py`, `scripts/probar_extractor.py`
- Test: `tests/test_extractor_220.py`

**Interfaces:**
- Consumes: SDK `anthropic` (`client.messages.parse` con documento PDF base64 y `output_format` pydantic), `IngresoLaboral`, `Fuente`.
- Produces: `Extraccion220` (pydantic: `empleador_nit, empleador_nombre, salarios, cesantias_e_intereses, prima, bonificaciones, aportes_salud, aportes_pension, retencion, confianza: float`); `extraer_220(pdf_bytes: bytes, client=None) -> IngresoLaboral` — el `client` inyectable permite testear sin red.
- El test unitario NO llama al API (cliente falso). La prueba real es manual con `scripts/probar_extractor.py` (requiere `ANTHROPIC_API_KEY` o perfil `ant auth login`).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_extractor_220.py
from declaras.extraccion.f220 import Extraccion220, extraer_220


class _RespuestaFalsa:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _MessagesFalso:
    def __init__(self, parsed):
        self._parsed = parsed
        self.llamadas = []

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        return _RespuestaFalsa(self._parsed)


class ClienteFalso:
    def __init__(self, parsed):
        self.messages = _MessagesFalso(parsed)


EXTRACCION = Extraccion220(
    empleador_nit="900123456", empleador_nombre="ACME SAS",
    salarios=120_000_000, cesantias_e_intereses=2_000_000, prima=1_000_000,
    bonificaciones=0, aportes_salud=4_800_000, aportes_pension=4_800_000,
    retencion=8_000_000, confianza=0.97,
)


def test_mapea_extraccion_a_ingreso_laboral():
    cliente = ClienteFalso(EXTRACCION)
    lab = extraer_220(b"%PDF-fake", client=cliente)
    assert lab.salarios == 120_000_000
    assert lab.bruto == 123_000_000
    assert lab.fuente.clase == "documento"
    assert lab.fuente.confianza == 0.97
    assert lab.fuente.detalle == "220"


def test_envia_pdf_como_documento_base64():
    cliente = ClienteFalso(EXTRACCION)
    extraer_220(b"%PDF-fake", client=cliente)
    llamada = cliente.messages.llamadas[0]
    contenido = llamada["messages"][0]["content"]
    assert contenido[0]["type"] == "document"
    assert contenido[0]["source"]["media_type"] == "application/pdf"
    assert llamada["output_format"] is Extraccion220
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_extractor_220.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/extraccion/f220.py
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
```

```python
# src/declaras/extraccion/__init__.py
from declaras.extraccion.f220 import Extraccion220, extraer_220

__all__ = ["Extraccion220", "extraer_220"]
```

```python
# scripts/probar_extractor.py
"""Prueba manual del extractor 220 contra el API real.

Uso: uv run python scripts/probar_extractor.py ruta/al/220.pdf
Requiere ANTHROPIC_API_KEY o perfil de `ant auth login`.
"""
import sys
from pathlib import Path

from declaras.extraccion import extraer_220

lab = extraer_220(Path(sys.argv[1]).read_bytes())
print(lab.model_dump_json(indent=2))
```

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/test_extractor_220.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/declaras/extraccion scripts tests/test_extractor_220.py
git commit -m "Extractor 220 con LLM: parse estructurado + proveniencia; cliente inyectable

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: API FastAPI + almacén JSON

**Files:**
- Create: `src/declaras/api/__init__.py`, `src/declaras/api/almacen.py`, `src/declaras/api/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: app FastAPI `declaras.api.main:app` con:
  - `POST /casos` (body `CasoTributario`) → `{"id": str}`
  - `GET /casos/{id}` → `CasoTributario`
  - `PUT /casos/{id}` (body `CasoTributario`) → reemplaza (la "entrada manual": el front edita hechos y re-sube el caso completo)
  - `POST /casos/{id}/liquidar` → `{"elecciones", "impuesto_neto", "saldo", "casillas", "flags", "combos_evaluados"}`
  - `GET /casos/{id}/borrador` → HTML
  - `GET /casos/{id}/memoria` → markdown (text/plain)
  - `POST /casos/{id}/documentos/220` (multipart `archivo`) → agrega el `IngresoLaboral` extraído y devuelve el caso
- `almacen.py`: `guardar(caso) -> str` (uuid4), `cargar(id) -> CasoTributario` (KeyError si no existe), `reemplazar(id, caso)`. Directorio: env `DECLARAS_DATOS` o `var/casos/`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_api.py
import pytest
from fastapi.testclient import TestClient

import declaras.api.main as api_main
from declaras.api.main import app
from declaras.caso import IngresoLaboral
from tests.golden.casos import FX, g1


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("DECLARAS_DATOS", str(tmp_path))
    return TestClient(app)


def _crear(cliente):
    r = cliente.post("/casos", json=g1().model_dump())
    assert r.status_code == 201
    return r.json()["id"]


def test_crear_y_leer_caso(cliente):
    caso_id = _crear(cliente)
    r = cliente.get(f"/casos/{caso_id}")
    assert r.status_code == 200
    assert r.json()["contribuyente"]["nombre"] == "G1 Asalariado"


def test_liquidar_devuelve_casillas_y_optimiza(cliente):
    caso_id = _crear(cliente)
    r = cliente.post(f"/casos/{caso_id}/liquidar")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["impuesto_neto"] == 1_495_977
    assert cuerpo["saldo"] == -6_504_023
    assert cuerpo["elecciones"] == {"usar_387": False, "usar_72uvt": True}
    assert any(c["codigo"] == "RLG_GENERAL" for c in cuerpo["casillas"])


def test_borrador_html(cliente):
    caso_id = _crear(cliente)
    r = cliente.get(f"/casos/{caso_id}/borrador")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "IMPUESTO_NETO" in r.text


def test_caso_inexistente_404(cliente):
    assert cliente.get("/casos/no-existe").status_code == 404


def test_subir_220_agrega_hecho(cliente, monkeypatch):
    caso_id = _crear(cliente)

    def _extraer_falso(pdf_bytes, client=None):
        from declaras.caso import Fuente
        return IngresoLaboral(
            empleador_nit="901", empleador_nombre="Otro Empleador",
            salarios=10_000_000, aportes_salud=400_000, aportes_pension=400_000,
            retencion=0, fuente=Fuente.documento("220", "abc123", confianza=0.9))

    monkeypatch.setattr(api_main, "extraer_220", _extraer_falso)
    r = cliente.post(f"/casos/{caso_id}/documentos/220",
                     files={"archivo": ("220.pdf", b"%PDF-fake", "application/pdf")})
    assert r.status_code == 200
    assert len(r.json()["laborales"]) == 2
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementación mínima**

```python
# src/declaras/api/almacen.py
import json
import os
import uuid
from pathlib import Path

from declaras.caso import CasoTributario


def _dir() -> Path:
    d = Path(os.environ.get("DECLARAS_DATOS", "var/casos"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def guardar(caso: CasoTributario) -> str:
    caso_id = uuid.uuid4().hex[:12]
    reemplazar(caso_id, caso)
    return caso_id


def reemplazar(caso_id: str, caso: CasoTributario) -> None:
    (_dir() / f"{caso_id}.json").write_text(caso.model_dump_json(indent=2))


def cargar(caso_id: str) -> CasoTributario:
    ruta = _dir() / f"{caso_id}.json"
    if not ruta.exists():
        raise KeyError(caso_id)
    return CasoTributario.model_validate(json.loads(ruta.read_text()))
```

```python
# src/declaras/api/main.py
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from declaras.api import almacen
from declaras.caso import CasoTributario
from declaras.extraccion import extraer_220
from declaras.optimizador import optimizar
from declaras.parametros import cargar as cargar_parametros
from declaras.render import borrador_html, casillas, memoria_markdown

app = FastAPI(title="declaras — demo", version="0.1.0")


def _caso(caso_id: str) -> CasoTributario:
    try:
        return almacen.cargar(caso_id)
    except KeyError:
        raise HTTPException(404, f"Caso {caso_id} no existe")


@app.post("/casos", status_code=201)
def crear_caso(caso: CasoTributario) -> dict:
    return {"id": almacen.guardar(caso)}


@app.get("/casos/{caso_id}")
def leer_caso(caso_id: str) -> CasoTributario:
    return _caso(caso_id)


@app.put("/casos/{caso_id}")
def reemplazar_caso(caso_id: str, caso: CasoTributario) -> CasoTributario:
    _caso(caso_id)  # 404 si no existe
    almacen.reemplazar(caso_id, caso)
    return caso


@app.post("/casos/{caso_id}/liquidar")
def liquidar_caso(caso_id: str) -> dict:
    caso = _caso(caso_id)
    p = cargar_parametros(caso.anio_gravable)
    r = optimizar(caso, p)
    return {
        "elecciones": r.elecciones.model_dump(),
        "combos_evaluados": r.evaluadas,
        "impuesto_neto": r.liquidacion.valor("IMPUESTO_NETO"),
        "saldo": r.liquidacion.valor("SALDO"),
        "casillas": casillas(r.liquidacion),
        "flags": [f.model_dump() for f in r.liquidacion.flags],
    }


@app.get("/casos/{caso_id}/borrador", response_class=HTMLResponse)
def borrador(caso_id: str) -> str:
    caso = _caso(caso_id)
    p = cargar_parametros(caso.anio_gravable)
    r = optimizar(caso, p)
    return borrador_html(r.liquidacion, caso)


@app.get("/casos/{caso_id}/memoria", response_class=PlainTextResponse)
def memoria(caso_id: str) -> str:
    caso = _caso(caso_id)
    p = cargar_parametros(caso.anio_gravable)
    r = optimizar(caso, p)
    return memoria_markdown(r.liquidacion, caso)


@app.post("/casos/{caso_id}/documentos/220")
def subir_220(caso_id: str, archivo: UploadFile) -> CasoTributario:
    caso = _caso(caso_id)
    laboral = extraer_220(archivo.file.read())
    caso.laborales.append(laboral)
    almacen.reemplazar(caso_id, caso)
    return caso
```

```python
# src/declaras/api/__init__.py
```

- [ ] **Step 4: Correr todo y ver el pass**

Run: `uv run pytest -q`
Expected: toda la suite verde (≈35 tests).

- [ ] **Step 5: Verificación manual del API**

Run: `uv run uvicorn declaras.api.main:app --port 8420` y en otra terminal:
`curl -s localhost:8420/docs > /dev/null && echo OK` — luego detener el server.

- [ ] **Step 6: Commit**

```bash
git add src/declaras/api tests/test_api.py
git commit -m "API FastAPI: casos CRUD, liquidar optimizado, borrador/memoria y upload 220

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review del plan (hecho al escribirlo)

1. **Cobertura del spec**: arquitectura §2 → Tasks 3–12; Caso §3 → Task 3 (+`Movimientos`, que el spec no listaba pero G2 exige — devolver al spec); parámetros §4 → Task 2; motor §5 → Tasks 5–9; optimizador §6 → Task 10; extracción §7 → Task 13 (220) + entrada manual vía `PUT /casos/{id}` en Task 14; golden §8 → Task 11; repos §9 → Tasks 1 y 14. Fuera y declarado: front, PDF nativo, extractores v1.1, mapeo casillas oficiales.
2. **Valores esperados verificados a mano**: tabla 241 (4 puntos), G1/G2/G3 completos (memoria en Task 11), pensiones mensuales, descuento 254-1 sobre y bajo umbral, anticipo por promedio.
3. **Consistencia de tipos**: códigos de nodo idénticos en Global Constraints, motor, render y golden; `Elecciones` y `Liquidacion` importados siempre de `declaras.motor`; `extraer_220(pdf_bytes, client=None)` igual en Task 13 y su uso en Task 14.
