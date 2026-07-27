# Conciliador + certificados del cliente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MVP operado por un contador, sin conversacional: leer la DIAN → derivar la lista de peticiones → recibir los documentos del cliente → conciliar → liquidar el 210 y mostrar la ganancia. Incluye unificar las dos ramas paralelas (conector DIAN de Juan + motor tributario), el **conciliador incremental**, los 9 extractores de certificados y el cableado de la consola del contador en el front.

**Architecture:** Base = arquitectura hexagonal de `origin/dev` (domain/adapters/services/api). Nuestro motor entra como paquete de cálculo intacto. El conciliador vive en `services/conciliacion/`; **nace con la consulta a la DIAN** (todas las partidas `SOLO_DIAN`) y **cada documento se incorpora de a uno**, actualizando partidas y recalculando la liquidación completa. Los extractores LLM se registran en el `documents/registry.py` existente, junto a los parsers deterministas del portal. Front: la consola de `declaras-front` (ya cableada al backend de Juan) gana las vistas de conciliación, peticiones y liquidación.

**El ciclo de vida, que es lo que define el diseño:**

```
llega la DIAN ──► partidas SOLO_DIAN ──► 210 PRELIMINAR + lista de peticiones
                        │                              │
                        │              el contador pide los documentos
                        │                              │
                        ▼                              ▼
        cada documento se INCORPORA (no re-cruza todo)
                        │
        SOLO_DIAN ──► COINCIDE (se cierra sola)
                 └──► DISCREPANCIA (va a la mesa del contador)
                        │
        recalcular liquidación ──► ganancia = preliminar − actual
                        │
        sin pendientes ──► 210 final + memoria de cálculo
```

**Tech Stack:** Python 3.12+ (piso de dev), pydantic v2, FastAPI async, SQLAlchemy async, pytest + pytest-asyncio, openpyxl/pypdf (parsers), SDK `anthropic` (extractores). Front: React 19 + Vite + react-router.

## Global Constraints

- **El motor no se toca.** `motor/`, `optimizador/`, `parametros/`, `render/`, `caso/` viajan tal cual. Los 6 goldens y los 189 tests deben seguir verdes con las mismas cifras. Cualquier cambio ahí exige justificación normativa.
- **Dinero**: int en pesos COP; único redondeo en `dinero.pesos()`; productos con `dinero.porcentaje()`.
- **Fail-loud**: ninguna cifra mala en silencio. Discrepancia sin resolver bloquea la liquidación; concepto no mapeado va al contador, no a un default.
- **Proveniencia obligatoria**: todo hecho del `CasoTributario` producido por el conciliador lleva `Fuente` que apunta a la partida que lo resolvió.
- **La clave DIAN nunca se persiste ni se loguea** (restricción heredada del doc maestro).
- Identificadores de dominio en español en lo nuevo nuestro (`Partida`, `Resolucion`); se respeta el inglés de los módulos de Juan (`Case`, `DocumentReading`) — no se renombra su código.
- Commits: mensaje en español, terminados en `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Rama de trabajo (back): `integracion`, creada desde `origin/dev` (su historia es la base; su trabajo en curso mergea más fácil). **La T1+T2 se empujan a `dev` el mismo día** para cerrar la ventana de divergencia con Juan; el resto va en ramas normales sobre la base unificada.
- Rama de trabajo (front): `dev` directamente — `origin/dev` desciende de `main`, no hay nada que fusionar.
- **Contratos entre dueños** (no se cruzan): `DocumentReading` es la frontera de entrada (sus parsers del portal / nuestros extractores de certificados); `CasoTributario` es la de salida (el conciliador lo produce, el motor lo consume). No se editan archivos del otro salvo los puntos de extensión que él dejó (`registry.py`, `documents_read.py`) y `tax/uvt.py` una única vez.
- **Los ahorros por petición NO son aditivos** (medido: sumarlos sobreestima hasta 64% cuando el tope del 40% se copa). La UI muestra `ahorro_estimado` como orientación, pero lo que se le dice al cliente es el **delta real** tras incorporar cada documento.
- **Fuera de alcance**: agente conversacional / WhatsApp, presentador en MUISCA, pagos, mapeo a casillas oficiales del 210, perfil independiente (`HONORARIOS`/`SERVICIOS` lanzan `NotImplementedError` a propósito).

---

### Task 1: Fusionar las dos ramas con todo verde

**Files:**
- Merge: `main` → rama `integracion` (desde `origin/dev`)
- Resolve: `pyproject.toml`, `.gitignore`, `src/declaras/__init__.py`, `uv.lock`
- Delete: `src/declaras/api/main.py`, `src/declaras/api/almacen.py`, `tests/test_api.py`
- Move: `tests/test_*.py` (nuestros) → `tests/unit/motor/`, `tests/unit/caso/`, etc. según su estructura
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: un paquete `declaras` con los 17 módulos, `uv run pytest -q` verde con la suma de las dos suites, `uv run ruff check` y `mypy` según config de dev.

- [ ] **Step 1: Crear la rama y hacer la fusión**

```bash
cd ~/Desktop/declaras/back
git fetch --all
git checkout -b integracion origin/dev
git merge --no-ff main   # 4 conflictos esperados, todos de infraestructura
```

- [ ] **Step 2: Resolver `pyproject.toml`**

Unión de dependencias, con estos criterios exactos:
- `requires-python = ">=3.12"` (piso de dev; nuestro código no usa nada de 3.13).
- Dependencias: las de dev **más** `pyyaml>=6`, `jinja2>=3.1`, `anthropic>=0.100` (que solo usa lo nuestro).
- `[dependency-groups] dev`: unión (pytest, pytest-asyncio, ruff, mypy, httpx).
- Conservar `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` de dev; en pytest agregar `asyncio_mode = "auto"` si dev no lo trae.
- `description` de dev.

- [ ] **Step 3: Resolver los otros tres conflictos**

- `.gitignore`: unión de líneas (dedup).
- `src/declaras/__init__.py`: el de dev (tiene `__version__` y probablemente exports); si el nuestro solo tenía `__version__ = "0.1.0"`, gana el de dev.
- `uv.lock`: **no editar a mano** — `rm uv.lock && uv lock` y verificar con `uv lock --check`.

- [ ] **Step 4: Borrar lo que el merge dejó redundante**

```bash
git rm src/declaras/api/main.py src/declaras/api/almacen.py tests/test_api.py
```

Razón: sus routers (`api/routers/cases.py`, `documents.py`) y su persistencia (`adapters/persistence/`) los reemplazan. La liquidación reaparece como router en la Task 6.

- [ ] **Step 5: Reubicar nuestros tests a su estructura**

Mover, sin cambiar contenido:
- `tests/test_parametros.py` → `tests/unit/parametros/test_parametros.py`
- `tests/test_caso.py` → `tests/unit/caso/test_caso.py`
- `tests/test_traza.py`, `test_general_base.py`, `test_rlg_general.py`, `test_pensiones.py`, `test_impuesto.py`, `test_cierre.py` → `tests/unit/motor/`
- `tests/test_optimizador.py` → `tests/unit/optimizador/`
- `tests/test_render.py` → `tests/unit/render/`
- `tests/test_extractor_220.py` → `tests/unit/documents/test_extractor_220.py`
- `tests/golden/` → `tests/golden/` (se queda donde está)
- `tests/test_smoke.py` → borrar (su suite ya cubre el import del paquete)

Agregar los `__init__.py` que falten. Los imports `from tests.test_rlg_general import ...` de `test_optimizador.py` deben actualizarse a la ruta nueva.

- [ ] **Step 6: Verificar que todo corre junto**

Run: `uv sync && uv run pytest -q`
Expected: la suma de las dos suites en verde (189 nuestros + los suyos), **cero fallos**. Los 6 goldens con las cifras idénticas.

Si algún test de dev falla por el merge (no por lo nuestro), arreglarlo; si algún test nuestro falla, **el bug está en la fusión**, no en el test.

- [ ] **Step 7: CI unificado**

`.github/workflows/ci.yml`: `uv sync --locked`, `uv run ruff check`, `uv run mypy src`, `uv run pytest -q`. Si `mypy` falla masivamente sobre nuestro código (no tenía type-checking), añadir nuestros módulos a un `[[tool.mypy.overrides]]` con `ignore_errors = true` y dejarlo anotado como deuda — no bloquear la fusión por eso.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Fusionar conector DIAN y motor tributario en un solo paquete

Base: arquitectura hexagonal de dev. El motor entra intacto (189 tests,
6 goldens con las mismas cifras). Se botan api/main.py y almacen.py:
los routers y la persistencia de dev los reemplazan.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Resolver los tres duplicados

**Files:**
- Modify: `src/declaras/parametros/__init__.py`, `src/declaras/parametros/modelos.py`, `src/declaras/parametros/ag2025.yaml`
- Modify: `src/declaras/tax/uvt.py` (pasa a delegar), `src/declaras/tax/obligation.py`
- Modify: `src/declaras/caso/fuentes.py` (proveniencia unificada)
- Test: `tests/unit/parametros/test_uvt_multianio.py`, `tests/unit/tax/test_obligation.py` (existente, ajustar)

**Interfaces:**
- Consumes: `ParametrosAnio`, `cargar(anio)`, `tax.uvt.uvt_for/in_pesos`, `tax.obligation.assess`.
- Produces: `parametros.uvt_de(anio) -> int` con la tabla multi-año 2019–2026; `tax/uvt.py` delegando a ella (sin su propia tabla); `Fuente` con campo `celda: str | None` para la granularidad de él.

- [ ] **Step 1: Escribir el test que falla (UVT multi-año)**

```python
# tests/unit/parametros/test_uvt_multianio.py
import pytest

from declaras.parametros import uvt_de


def test_uvt_por_anio():
    assert uvt_de(2025) == 49_799
    assert uvt_de(2026) == 52_374
    assert uvt_de(2019) == 34_270


def test_anio_sin_uvt_revienta():
    with pytest.raises(ValueError, match="2018"):
        uvt_de(2018)


def test_tax_uvt_delega_en_parametros():
    """tax/uvt.py no puede tener su propia tabla: una sola fuente de verdad."""
    from declaras.tax import uvt as tax_uvt

    assert tax_uvt.uvt_for(2025) == uvt_de(2025)
    assert not hasattr(tax_uvt, "UVT_BY_YEAR")
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/parametros/test_uvt_multianio.py -q`
Expected: FAIL — `uvt_de` no existe.

- [ ] **Step 3: Implementar**

En `parametros/__init__.py` agregar la tabla (tomada de `tax/uvt.py`) y la función:

```python
# Valor de la UVT por año gravable, en pesos. Conviven dos en cualquier momento: la del
# año que se declara (para sus topes) y la del año en curso (sanciones, planeación).
UVT_POR_ANIO: dict[int, int] = {
    2019: 34_270, 2020: 35_607, 2021: 36_308, 2022: 38_004,
    2023: 42_412, 2024: 47_065, 2025: 49_799, 2026: 52_374,
}


def uvt_de(anio: int) -> int:
    """UVT del año gravable indicado."""
    valor = UVT_POR_ANIO.get(anio)
    if valor is None:
        raise ValueError(
            f"No hay UVT registrada para el año {anio}; disponibles: "
            f"{sorted(UVT_POR_ANIO)}"
        )
    return valor
```

En `tax/uvt.py`: borrar `UVT_BY_YEAR` y hacer que `uvt_for` delegue en `uvt_de`, conservando el tipo de excepción que sus tests esperan (`ValidationError` de su `domain.errors`), envolviendo el `ValueError`. `in_pesos` se conserva tal cual (su semántica de no redondear es correcta y sus tests la fijan).

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/unit/parametros tests/unit/tax -q`
Expected: PASS, incluidos los tests suyos de `uvt` y `obligation` sin modificar.

- [ ] **Step 5: Unificar la proveniencia**

En `caso/fuentes.py`, agregar a `Fuente` el campo opcional que aporta su granularidad:

```python
    celda: str | None = None  # celda del XLSX o casilla del formulario (su ExtractedField.source)
```

Y un constructor nuevo:

```python
    @classmethod
    def conciliacion(cls, partida_id: str, detalle: str) -> "Fuente":
        """Hecho producido por una resolución del contador sobre una partida."""
        return cls(clase="conciliacion", ref=partida_id, detalle=detalle)
```

Ampliar el `Literal` de `clase` con `"conciliacion"`. + test de que un `Fuente.conciliacion` valida y que `celda` es opcional (no rompe los 6 goldens, que no lo usan).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Unificar UVT multi-año, delegar tax/uvt y ampliar Fuente con celda y conciliación

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Registrar el extractor 220 en el registry de documentos

**Files:**
- Create: `src/declaras/documents/parsers/certificados.py`
- Modify: `src/declaras/documents/registry.py`, `src/declaras/extraccion/f220.py`
- Test: `tests/unit/documents/test_lectura_certificado.py`

**Interfaces:**
- Consumes: `extraer_220(pdf_bytes, anio_esperado, client)` (existente), `DocumentReading`, `ExtractedField`, `Confidence`.
- Produces: `certificados.leer_220(content: bytes, *, anio_esperado: int | None = None, client=None) -> DocumentReading`; en `registry.py` un `LLM_READERS: dict[str, Reader]` con `"CERT_INGRESOS_220"`, y `reader_for` que busca en las dos familias; `is_deterministic` sin cambios.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/unit/documents/test_lectura_certificado.py
from declaras.documents import registry
from declaras.documents.parsers import certificados
from declaras.extraccion.f220 import Extraccion220
from tests.unit.documents.dobles import ClienteFalso  # reusar el doble existente

EXTRACCION = Extraccion220(
    empleador_nit="900123456", empleador_nombre="ACME SAS",
    salarios=85_000_000, cesantias_e_intereses=0, prima=0, bonificaciones=0,
    total_ingresos_brutos=85_000_000, pensiones_de_jubilacion=0,
    aportes_salud=3_400_000, aportes_pension=3_400_000, retencion=8_000_000,
    anio_gravable=2025, numero_de_certificados=1, confianza=0.97,
)


def test_lectura_220_produce_document_reading():
    lectura = certificados.leer_220(b"%PDF-x", client=ClienteFalso(EXTRACCION))
    assert lectura.doc_type == "CERT_INGRESOS_220"
    assert lectura.field("salarios") == 85_000_000
    assert lectura.field("empleador_nit") == "900123456"
    # La confianza del modelo viaja en cada campo, no se pierde.
    campo = next(f for f in lectura.fields if f.name == "salarios")
    assert campo.confidence == 0.97


def test_registry_conoce_el_220_y_no_lo_llama_deterministico():
    assert registry.reader_for("CERT_INGRESOS_220") is not None
    assert not registry.is_deterministic("CERT_INGRESOS_220")
    assert "EXOGENA" in registry.supported_types()
    assert "CERT_INGRESOS_220" in registry.supported_types()
```

(Si no existe `tests/unit/documents/dobles.py`, crearlo extrayendo el `ClienteFalso` de `test_extractor_220.py` para no duplicarlo.)

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/documents/test_lectura_certificado.py -q`
Expected: FAIL — `certificados` no existe.

- [ ] **Step 3: Implementar**

`certificados.py` envuelve el extractor existente y traduce a `DocumentReading`:

```python
"""Lectores de certificados que aporta el cliente.

A diferencia de los documentos del portal (columnas fijas, parser determinístico), cada
emisor arma su certificado como quiere: se leen con un modelo y por eso cada campo viaja
con la confianza que el modelo declaró.
"""
from declaras.documents.models import DocumentReading, ExtractedField
from declaras.extraccion.f220 import extraer_220, id_documento

PARSER_220 = "cert_220.llm.v1"


def leer_220(content: bytes, *, anio_esperado: int | None = None, client=None) -> DocumentReading:
    laboral = extraer_220(content, anio_esperado=anio_esperado, client=client)
    confianza = laboral.fuente.confianza or 0.0
    campos = {
        "empleador_nit": laboral.empleador_nit,
        "empleador_nombre": laboral.empleador_nombre,
        "salarios": laboral.salarios,
        "cesantias_e_intereses": laboral.cesantias_e_intereses,
        "prima": laboral.prima,
        "bonificaciones": laboral.bonificaciones,
        "aportes_salud": laboral.aportes_salud,
        "aportes_pension": laboral.aportes_pension,
        "retencion": laboral.retencion,
    }
    return DocumentReading(
        doc_type="CERT_INGRESOS_220",
        parser=PARSER_220,
        content_sha256=id_documento(content),
        fields=[ExtractedField(name=k, value=v, confidence=confianza) for k, v in campos.items()],
    )
```

En `registry.py` agregar la familia LLM y que `reader_for` consulte ambas, sin tocar `is_deterministic`.

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/unit/documents -q`
Expected: PASS, incluidos sus tests de registry y los 28 del extractor 220.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Registrar el lector del 220 en el registry: familia LLM junto a la determinística

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: El conciliador — Partida y cruce por NIT + concepto

**Files:**
- Create: `src/declaras/services/conciliacion/__init__.py`, `modelos.py`, `conceptos.py`, `cruce.py`
- Test: `tests/unit/conciliacion/test_cruce.py`, `tests/unit/conciliacion/test_conceptos.py`

**Interfaces:**
- Consumes: `DocumentReading` (con `rows` de exógena y `fields` de certificados), `Fuente`.
- Produces:
  - `Concepto` (StrEnum): `SALARIOS, HONORARIOS, SERVICIOS, ARRENDAMIENTOS, RENDIMIENTOS, DIVIDENDOS, PENSIONES, APORTES_SALUD, APORTES_PENSION, RETENCION, OTROS`.
  - `concepto_de_codigo(code: str) -> Concepto | None` — mapa de códigos oficiales conocidos; `None` = no mapeado (va al contador, nunca a un default).
  - `Lado` (StrEnum): `DIAN`, `DOCUMENTO`.
  - `Valor{monto: int, retencion: int, lado: Lado, celda: str | None, confianza: float | None}`.
  - `EstadoPartida` (StrEnum): `COINCIDE, DISCREPANCIA, SOLO_DIAN, SOLO_DOCUMENTO, CONCEPTO_DESCONOCIDO`.
  - `Partida{id: str, nit_tercero: str, nombre_tercero: str, concepto: Concepto | None, codigos_crudos: list[str], version_dian: Valor | None, version_documento: Valor | None, estado: EstadoPartida, nota: str | None, resolucion: Resolucion | None}` con properties `.diferencia_monto -> int` y `.diferencia_retencion -> int`.
  - **`abrir(exogena: DocumentReading) -> list[Partida]`** — fase 1: todas nacen `SOLO_DIAN` (o `CONCEPTO_DESCONOCIDO`).
  - **`incorporar(partidas, documento: DocumentReading, *, tolerancia_pesos: int = 1000) -> list[Partida]`** — fase 2: un documento a la vez; la partida que empareja cambia de estado, y si no empareja ninguna nace una `SOLO_DOCUMENTO`.
- Reglas:
  - **La llave es `(nit, Concepto normalizado)`, no el código crudo.** Dos códigos que normalizan al mismo concepto (5002 honorarios, 5003 comisiones) son UNA partida; los códigos quedan en `codigos_crudos`. El `id` es `f"{nit}:{concepto}"`, estable.
  - **Se comparan dos números: monto y retención.** `DISCREPANCIA` si cualquiera excede la tolerancia — declarar más retención de la reportada casi garantiza requerimiento, así que va expuesta aparte.
  - Varias filas de exógena de la misma llave se **suman** (un tercero reporta el mismo concepto en varias filas).
  - Filas cuyo `reported_id_number` no sea el del contribuyente **nunca aportan hecho**: `SOLO_DIAN` con `nota` "reportado a otra identificación" (el caso que su parser ya detecta).
  - **No hay operación en lote**: los documentos llegan de a uno, en días distintos. Un `cruzar(exogena, certificados)` que asuma los dos lados presentes es incorrecto.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/unit/conciliacion/test_cruce.py
from declaras.documents.models import DocumentReading, ExtractedField, ExtractedRow
from declaras.services.conciliacion import Concepto, EstadoPartida, Lado, abrir, incorporar


def _exogena(*filas: dict) -> DocumentReading:
    return DocumentReading(
        doc_type="EXOGENA", parser="test", content_sha256="a" * 64,
        fields=[ExtractedField(name="id_number", value="1234567")],
        rows=[ExtractedRow(values=f, source=f"A{i}") for i, f in enumerate(filas, 20)],
    )


def _fila(nit, codigo, monto, retencion=0, reportado_a="1234567", nombre="ACME SAS"):
    return {
        "reporter_nit": nit, "reporter_name": nombre,
        "reported_id_number": reportado_a, "reported_name": "PRUEBA",
        "concept": f"X (Concepto: {codigo})", "concept_code": codigo,
        "amount": monto, "retencion": retencion,
        "suggested_use": "Tope 1: Ingresos brutos | R32 Ingresos brutos",
    }


def _cert_220(nit, salarios, retencion=0):
    campos = {"empleador_nit": nit, "empleador_nombre": "ACME SAS",
              "salarios": salarios, "retencion": retencion}
    return DocumentReading(
        doc_type="CERT_INGRESOS_220", parser="test", content_sha256="b" * 64,
        fields=[ExtractedField(name=k, value=v, confidence=0.97) for k, v in campos.items()],
    )


def test_abrir_deja_todo_en_solo_dian():
    """Fase 1: solo hay DIAN. Nada puede estar conciliado todavía."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000),
                              _fila("890903938", "5010", 8_000_000)))
    assert {p.estado for p in partidas} == {EstadoPartida.SOLO_DIAN}
    assert {p.concepto for p in partidas} == {Concepto.SALARIOS, Concepto.RENDIMIENTOS}


def test_incorporar_un_documento_que_confirma():
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_500)))
    partidas = incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert len(partidas) == 1
    assert partidas[0].estado == EstadoPartida.COINCIDE
    assert partidas[0].concepto == Concepto.SALARIOS


def test_discrepancia_expone_las_dos_versiones():
    partidas = abrir(_exogena(_fila("900111222", "5001", 87_400_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert p.estado == EstadoPartida.DISCREPANCIA
    assert p.version_dian.monto == 87_400_000
    assert p.version_documento.monto == 85_000_000
    assert p.diferencia_monto == 2_400_000
    assert p.version_dian.lado is Lado.DIAN


def test_discrepancia_solo_en_la_retencion():
    """Monto igual, retención distinta: sigue siendo discrepancia y se ve aparte."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000, retencion=8_000_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000, retencion=6_000_000))
    assert p.estado == EstadoPartida.DISCREPANCIA
    assert p.diferencia_monto == 0
    assert p.diferencia_retencion == 2_000_000


def test_dos_codigos_del_mismo_concepto_son_una_partida():
    partidas = abrir(_exogena(_fila("901222333", "5002", 10_000_000),
                              _fila("901222333", "5003", 4_000_000)))
    assert len(partidas) == 1
    assert partidas[0].version_dian.monto == 14_000_000
    assert sorted(partidas[0].codigos_crudos) == ["5002", "5003"]


def test_solo_documento_de_un_beneficio_que_la_dian_no_ve():
    """Un certificado que nadie pidió y que la DIAN no puede conocer."""
    partidas = incorporar(abrir(_exogena()), _cert_220("900111222", 85_000_000))
    assert partidas[0].estado == EstadoPartida.SOLO_DOCUMENTO


def test_reportado_a_otra_identificacion_no_se_cruza():
    partidas = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    [p] = incorporar(partidas, _cert_220("901999888", 9_000_000))
    assert p.estado == EstadoPartida.SOLO_DIAN
    assert "otra identificación" in (p.nota or "")


def test_concepto_desconocido_no_se_asume():
    [p] = abrir(_exogena(_fila("900111222", "9999", 5_000_000)))
    assert p.estado == EstadoPartida.CONCEPTO_DESCONOCIDO
    assert p.concepto is None
    assert p.codigos_crudos == ["9999"]


def test_id_de_partida_es_estable_y_por_concepto():
    ex = _exogena(_fila("900111222", "5001", 87_400_000))
    assert abrir(ex)[0].id == abrir(ex)[0].id == "900111222:SALARIOS"
```

```python
# tests/unit/conciliacion/test_conceptos.py
from declaras.services.conciliacion import Concepto, concepto_de_codigo


def test_codigos_conocidos():
    assert concepto_de_codigo("5001") is Concepto.SALARIOS
    assert concepto_de_codigo("5004") is Concepto.SERVICIOS


def test_codigo_desconocido_devuelve_none_no_un_default():
    assert concepto_de_codigo("9999") is None
    assert concepto_de_codigo("") is None
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/conciliacion -q`
Expected: FAIL — el paquete no existe.

- [ ] **Step 3: Implementar**

`conceptos.py` con el mapa de códigos oficiales conocidos → `Concepto`, y `concepto_de_codigo` que devuelve `None` para lo no mapeado (nunca `OTROS` por defecto: un código desconocido es una pregunta al contador, no una categoría).

**Códigos a mapear** (los verificables desde los fixtures de su parser y el formato de terceros; cualquier otro queda sin mapear a propósito):
`5001→SALARIOS`, `5002→HONORARIOS`, `5003→COMISIONES` (trátalo como `HONORARIOS` si no agregas el miembro), `5004→SERVICIOS`, `5005→ARRENDAMIENTOS`, `5010→RENDIMIENTOS`, `5016→OTROS`.
Deja un comentario diciendo que la tabla es incremental y que un código sin mapear es el comportamiento correcto, no un hueco.

`modelos.py` con los pydantic (`extra="forbid"`) descritos en Interfaces. `Partida.diferencia_monto` = `abs(dian.monto − doc.monto)` y `.diferencia_retencion` = `abs(dian.retencion − doc.retencion)` cuando las dos versiones existen; 0 si falta alguna.

`cruce.py` con las dos operaciones:

`abrir(exogena)`:
1. Lee la identificación del contribuyente de `exogena.field("id_number")`.
2. Recorre `exogena.rows`, normaliza `concept_code` → `Concepto`, agrupa por `(reporter_nit, concepto)` **sumando** monto y retención.
3. Cada grupo nace `SOLO_DIAN`, o `CONCEPTO_DESCONOCIDO` si `concepto is None`.
4. Grupos con `reported_id_number` ≠ contribuyente llevan `nota` y no aportarán hecho.

`incorporar(partidas, documento)`:
1. Deriva `(nit, concepto)` del documento con la tabla `TIPO_A_CLAVE` (`doc_type` → campo del NIT + `Concepto`).
2. Si existe partida con ese `id`: adjunta `version_documento` y reclasifica — `COINCIDE` si monto y retención están dentro de la tolerancia, `DISCREPANCIA` si no.
3. Si no existe: nace `SOLO_DOCUMENTO`.
4. Si el documento no trae NIT (entrada manual sin la llave): no empareja, nace `SOLO_DOCUMENTO` con `nota` de que no se pudo cruzar.

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/unit/conciliacion -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Conciliador: partidas cruzadas por NIT y concepto, con los cinco desenlaces

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Resoluciones, respuestas del cliente y mapeo a CasoTributario

**Files:**
- Create: `src/declaras/services/conciliacion/resolucion.py`, `src/declaras/services/conciliacion/mapeo.py`, `src/declaras/services/conciliacion/respuestas.py`
- Modify: `src/declaras/services/conciliacion/modelos.py` (agregar `Resolucion`)
- Test: `tests/unit/conciliacion/test_resolucion.py`, `tests/unit/conciliacion/test_mapeo.py`

**Interfaces:**
- Consumes: `Partida`, `EstadoPartida`, `Concepto`, `CasoTributario` y sus modelos, `Fuente.conciliacion`.
- Produces:
  - `Decision` (StrEnum): `USAR_DIAN, USAR_DOCUMENTO, USAR_OTRO, MARCAR_AJENO, CERRAR_SIN_SOPORTE`.
  - `Motivo` (StrEnum): `COINCIDEN, ERROR_DEL_TERCERO, ERROR_DEL_CERTIFICADO, NO_ES_MIO, FALTA_DOCUMENTO, DECISION_DEL_CONTADOR`.
  - `Origen` (StrEnum): `SISTEMA` (provisional, puesta al abrir por falta de documento) y `CONTADOR` (decisión de una persona).
  - `Resolucion{decision, valor: int, motivo, origen: Origen, huella: str, nota: str | None, quien: str, cuando: datetime}` (`extra="forbid"`). La `huella` es un hash de las dos versiones que el resolvedor vio.
  - `Respuesta{pregunta: str, tiene: bool, detalle: dict, quien: str, cuando: datetime}` — lo que el cliente contestó a las preguntas básicas. **Sin esto el sistema pregunta por prepagada para siempre.**
  - `refrescar(nuevas, guardadas) -> list[Partida]` — al incorporar un documento o re-consultar la DIAN: una resolución de `origen=SISTEMA` se **reemplaza siempre** (era provisional); una de `origen=CONTADOR` se preserva **solo si la huella coincide**, y si no, la partida vuelve a pendiente con `nota` "los valores cambiaron desde la resolución anterior".
  - `resolver(partida, decision, *, motivo, quien, valor=None, nota=None) -> Partida` — pura, devuelve una copia resuelta; valida que la decisión sea posible para el estado (ej. `USAR_DOCUMENTO` sobre `SOLO_DIAN` → `ValueError`), y que `USAR_OTRO` traiga `valor`.
  - `autorresolver(partidas) -> list[Partida]` — dos automatismos: las `COINCIDE` se cierran (`USAR_DOCUMENTO`, `motivo=COINCIDEN`, `origen=SISTEMA`), y las `SOLO_DIAN` reciben una **provisional** `USAR_DIAN` `origen=SISTEMA` para que el 210 preliminar exista sin esperar documentos. Las `DISCREPANCIA` y `CONCEPTO_DESCONOCIDO` quedan pendientes de persona.
  - `pendientes(partidas) -> list[Partida]` ordenadas por `diferencia` descendente (la plata en juego primero).
  - `a_caso(partidas, *, contribuyente, anio_gravable, beneficios=None, patrimonio=None, creditos=None) -> CasoTributario` — exige que no queden pendientes (`ValueError` con el conteo si quedan) y que ninguna partida resuelta tenga concepto `None`.
- Reglas:
  - `MARCAR_AJENO` y `CERRAR_SIN_SOPORTE` **no aportan hecho** al Caso; las demás sí, con `Fuente.conciliacion(partida.id, detalle)`.
  - **`a_caso` agrupa por NIT, no por concepto.** Un `IngresoLaboral` se ensambla con las partidas de `SALARIOS` + `APORTES_SALUD` + `APORTES_PENSION` + retención **del mismo tercero**: en la exógena esos son conceptos distintos en filas distintas.
  - **Pensión resuelta solo con datos de la DIAN**: la exógena da el total anual y la exención es mensual. Se reparte en 12 mesadas iguales y se levanta flag `PENSION_DISTRIBUIDA_UNIFORME` (sale impreso en el borrador): correcto en el caso común, equivocado si hubo retroactivo, y el contador tiene que verlo.
  - `a_caso` exige que no queden pendientes de persona; las provisionales del sistema **sí** liquidan (son el preliminar).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/unit/conciliacion/test_resolucion.py
import pytest

from declaras.services.conciliacion import (
    Decision, EstadoPartida, Motivo, autorresolver, pendientes, resolver,
)
from tests.unit.conciliacion.fabricas import partida_discrepancia, partida_coincide, partida_solo_dian


def test_autorresuelve_solo_las_coincidentes():
    resueltas = autorresolver([partida_coincide(), partida_discrepancia()])
    assert resueltas[0].resolucion.motivo is Motivo.COINCIDEN
    assert resueltas[0].resolucion.quien == "sistema"
    assert resueltas[1].resolucion is None


def test_resolver_discrepancia_a_favor_del_documento():
    p = resolver(partida_discrepancia(), Decision.USAR_DOCUMENTO,
                 motivo=Motivo.ERROR_DEL_TERCERO, quien="contador@x.co")
    assert p.resolucion.valor == 85_000_000
    assert p.resolucion.decision is Decision.USAR_DOCUMENTO


def test_no_se_puede_usar_documento_que_no_existe():
    with pytest.raises(ValueError, match="SOLO_DIAN"):
        resolver(partida_solo_dian(), Decision.USAR_DOCUMENTO,
                 motivo=Motivo.ERROR_DEL_TERCERO, quien="x")


def test_usar_otro_exige_valor():
    with pytest.raises(ValueError, match="valor"):
        resolver(partida_discrepancia(), Decision.USAR_OTRO,
                 motivo=Motivo.DECISION_DEL_CONTADOR, quien="x")


def test_pendientes_ordena_por_plata_en_juego():
    ps = pendientes([partida_discrepancia(diferencia=100),
                     partida_discrepancia(diferencia=9_000_000)])
    assert ps[0].diferencia_monto == 9_000_000
```

```python
# tests/unit/conciliacion/test_mapeo.py
import pytest

from declaras.caso import Contribuyente
from declaras.services.conciliacion import Decision, Motivo, a_caso, autorresolver, resolver
from tests.unit.conciliacion.fabricas import partida_coincide, partida_discrepancia, partida_solo_dian

CONTRIB = Contribuyente(num_doc="1234567", nombre="Prueba")


def test_no_se_puede_liquidar_con_partidas_pendientes():
    with pytest.raises(ValueError, match="1 partida"):
        a_caso([partida_discrepancia()], contribuyente=CONTRIB, anio_gravable=2025)


def test_partida_resuelta_produce_hecho_con_proveniencia():
    caso = a_caso(autorresolver([partida_coincide()]), contribuyente=CONTRIB, anio_gravable=2025)
    assert len(caso.laborales) == 1
    lab = caso.laborales[0]
    assert lab.salarios == 85_000_000
    assert lab.fuente.clase == "conciliacion"
    assert lab.fuente.ref == "900111222:SALARIOS"


def test_ajeno_no_entra_al_caso():
    p = resolver(partida_solo_dian(), Decision.MARCAR_AJENO, motivo=Motivo.NO_ES_MIO, quien="x")
    caso = a_caso([p], contribuyente=CONTRIB, anio_gravable=2025)
    assert caso.laborales == []
    assert caso.ingresos_brutos_totales == 0
```

Crear `tests/unit/conciliacion/fabricas.py` con las tres fábricas de partidas (reusando los helpers de `test_cruce.py`).

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/conciliacion -q`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`resolucion.py` con `Decision`, `Motivo`, `Resolucion`, `resolver`, `autorresolver`, `pendientes`. Tabla de decisiones válidas por estado:

| Estado | Decisiones permitidas |
|---|---|
| `COINCIDE` | `USAR_DOCUMENTO`, `USAR_DIAN` |
| `DISCREPANCIA` | `USAR_DOCUMENTO`, `USAR_DIAN`, `USAR_OTRO` |
| `SOLO_DIAN` | `USAR_DIAN`, `MARCAR_AJENO`, `USAR_OTRO` |
| `SOLO_DOCUMENTO` | `USAR_DOCUMENTO`, `USAR_OTRO` |
| `CONCEPTO_DESCONOCIDO` | `MARCAR_AJENO`, `CERRAR_SIN_SOPORTE` (no puede aportar hecho: no se sabe a qué cédula va) |

`mapeo.py` con `a_caso`: agrupa las partidas resueltas por `Concepto` y construye los modelos del Caso. Para v1 mapea `SALARIOS → IngresoLaboral`, `RENDIMIENTOS → Rendimiento`, `ARRENDAMIENTOS → Arriendo`, `DIVIDENDOS → Dividendo`, `PENSIONES → IngresoPension`; `HONORARIOS/SERVICIOS/COMISIONES/OTROS` **no tienen modelo en el Caso todavía** (el motor no cubre independientes) → si aparecen resueltas con hecho, lanzar `NotImplementedError` con el concepto, que es honesto y ruidoso. Los `beneficios`, `patrimonio` y `creditos` entran como parámetros (vienen de otras fuentes, no del cruce).

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/unit/conciliacion -q && uv run pytest tests/golden -q`
Expected: todo verde; los goldens intactos.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Conciliador: resoluciones del contador y mapeo a CasoTributario con proveniencia

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: API — peticiones, incorporación de documentos y liquidación versionada

**Files:**
- Create: `src/declaras/api/routers/conciliacion.py`, `src/declaras/api/conciliacion_schemas.py`, `src/declaras/services/conciliacion/peticiones.py`, `src/declaras/services/conciliacion/liquidaciones.py`
- Modify: `src/declaras/api/app.py`, `src/declaras/api/routers/documents.py`, `src/declaras/adapters/persistence/tables.py`
- Test: `tests/unit/conciliacion/test_peticiones.py`, `tests/integration/test_conciliacion_api.py`

**Interfaces:**
- `derivar_peticiones(partidas, respuestas, caso) -> list[Peticion]` — **derivada, no almacenada**. Tres orígenes:
  1. Cada partida `SOLO_DIAN` cuyo certificado aporte algo → petición con el nombre y NIT del tercero.
  2. Cada beneficio invisible sin `Respuesta` registrada → petición con `pregunta_previa`.
  3. Condicionales: si `Respuesta.tiene is True` y falta el documento → petición del certificado.
  Una `Respuesta.tiene is False` **apaga la petición para siempre**.
- `Peticion{id, tipo_documento, tercero: {nit, nombre} | None, razon, ahorro_estimado: int, prioridad: int, pregunta_previa: str | None, copy_sugerido: str}`. `ahorro_estimado` sale de `optimizador.ahorro_marginal`; se ordena descendente y se corta por umbral (`>= 50_000`) y cantidad (10).
- `liquidar_y_versionar(caso, partidas) -> Liquidacion versionada` — guarda cada liquidación con su momento. `GET /liquidacion` devuelve `{preliminar, actual, ganancia}` donde `ganancia = preliminar.impuesto − actual.impuesto`.
- Endpoints bajo `/v1/cases/{case_id}`:
  - `POST /conciliacion` → `abrir(exógena del expediente)` + `autorresolver` + persiste + liquida el preliminar. Devuelve `{total, pendientes, por_estado}`.
  - `GET /conciliacion` → partidas ordenadas por plata en juego.
  - `POST /conciliacion/{partida_id}/resolver` → `{decision, motivo, valor?, nota?}`. 409 si la decisión no aplica al estado; recalcula la liquidación.
  - `GET /peticiones` → la lista derivada, priorizada.
  - `POST /respuestas` → `{pregunta, tiene, detalle?}` — registra lo que contestó el cliente.
  - `GET /liquidacion` → `{preliminar, actual, ganancia}`.
  - `POST /cerrar-peticion/{peticion_id}` → cierra sin soporte, **devolviendo el costo** (diferencia de liquidar con y sin ese beneficio).
  - `GET /borrador` (HTML) y `GET /memoria` (texto).
- `POST /v1/cases/{id}/documents` (el existente de Juan) pasa a aceptar **varios archivos** y un `peticion_id` opcional por archivo; por cada uno devuelve `{archivo, doc_type, estado: emparejado|sin_emparejar|a_bandeja, peticion_cerrada?, motivo?}`. Tras incorporar, corre `refrescar` y recalcula.
- Persistencia: tablas de partidas, respuestas y liquidaciones, siguiendo el patrón de `case_repository.py`.

- [ ] **Step 1: Escribir los tests que fallan**

`test_peticiones.py` (unitario, sin API):

```python
def test_partida_solo_dian_genera_peticion_con_el_tercero():
    ps = derivar_peticiones(abrir(_exogena(_fila("900111222", "5001", 87_400_000))), [], CASO)
    assert any(p.tercero["nit"] == "900111222" for p in ps)


def test_beneficio_invisible_pregunta_antes_de_pedir():
    ps = derivar_peticiones([], [], CASO)
    prepagada = next(p for p in ps if p.tipo_documento == "CERT_PREPAGADA")
    assert prepagada.pregunta_previa is not None


def test_respuesta_negativa_apaga_la_peticion_para_siempre():
    respuestas = [Respuesta(pregunta="PREPAGADA", tiene=False, detalle={}, quien="c", cuando=AHORA)]
    ps = derivar_peticiones([], respuestas, CASO)
    assert not any(p.tipo_documento == "CERT_PREPAGADA" for p in ps)


def test_se_ordenan_por_ahorro_y_se_corta_por_umbral():
    ps = derivar_peticiones(PARTIDAS_VARIAS, [], CASO)
    assert [p.ahorro_estimado for p in ps] == sorted((p.ahorro_estimado for p in ps), reverse=True)
    assert all(p.ahorro_estimado >= 50_000 or p.ahorro_estimado == 0 for p in ps)
    assert len(ps) <= 10
```

`test_conciliacion_api.py` (integración, con el estilo de `test_documents_api.py` existente), 10 casos:
1. `POST /conciliacion` sobre un expediente con exógena → 200, todas `SOLO_DIAN`, provisionales puestas.
2. `GET /liquidacion` inmediatamente después → `preliminar` poblado, `ganancia == 0`.
3. `GET /peticiones` → incluye la del 220 de ACME y la de prepagada.
4. Subida **masiva** de 3 archivos → por cada uno su desenlace; el 220 empareja y crea `DISCREPANCIA`.
5. `GET /liquidacion` → `actual` cambió y `ganancia > 0`.
6. `POST resolver` con decisión inválida para el estado → 409.
7. `POST resolver` válido → 200, la partida sale de pendientes.
8. `POST /respuestas` con `tiene=False` → la petición desaparece de `GET /peticiones`.
9. `POST /cerrar-peticion` → 200 con el costo calculado.
10. **Idempotencia**: re-`POST /conciliacion` preserva la resolución del contador y reemplaza las provisionales del sistema.

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/conciliacion/test_peticiones.py tests/integration/test_conciliacion_api.py -q`
Expected: FAIL — nada existe.

- [ ] **Step 3: Implementar**

Router + schemas (`extra="forbid"`) + tablas + repositorios + registro en `app.py`. Los `ValueError` del conciliador → 409 vía el `register_exception_handlers` existente. El `copy_sugerido` de cada tipo de petición sale de una tabla de textos (es el copy del doc maestro, no improvisado).

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest -q`
Expected: suite completa verde; los 6 goldens intactos.

- [ ] **Step 5: Verificación manual**

Levantar la app, sembrar un expediente con la exógena de fixture, y correr el flujo entero por curl: conciliar → ver preliminar → ver peticiones → subir 2 documentos → ver la ganancia → resolver → cerrar → abrir el borrador. Dejar los comandos exactos en el reporte.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "API: peticiones derivadas, incorporación masiva de documentos y liquidación versionada

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: NIT en los tres modelos que no lo llevan

**Files:**
- Modify: `src/declaras/caso/modelos.py`
- Test: `tests/unit/caso/test_caso.py`

**Interfaces:**
- Produces: `IngresoPension.pagador_nit: str | None`, `Rendimiento.entidad_nit: str | None`, `Arriendo.contraparte_nit: str | None` (+ `contraparte_nombre: str | None`).
- Regla: **opcionales**, para no romper los 6 goldens ni la entrada manual; los extractores los llenan siempre. Sin NIT una partida no se puede cruzar — eso lo señala el conciliador, no el schema.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_ingresos_llevan_nit_opcional_para_el_cruce():
    p = IngresoPension(pagador="Colpensiones", pagador_nit="900123456",
                       mesadas=[10_000_000] * 12, fuente=FX)
    assert p.pagador_nit == "900123456"
    # Opcional: la entrada manual puede no tenerlo.
    assert IngresoPension(pagador="X", mesadas=[0] * 12, fuente=FX).pagador_nit is None
```

Análogos para `Rendimiento` y `Arriendo`.

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/caso -q`
Expected: FAIL — `extra="forbid"` rechaza el campo nuevo.

- [ ] **Step 3: Implementar**

Agregar los campos con `default=None`.

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest -q`
Expected: todo verde, **los 6 goldens con las mismas cifras** (el campo es opcional y nadie lo lee todavía).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Caso: NIT opcional en pensiones, rendimientos y arriendos (llave de cruce)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Infraestructura compartida de extractores LLM

**Files:**
- Create: `src/declaras/extraccion/_base.py`
- Modify: `src/declaras/extraccion/f220.py` (usar la base)
- Test: `tests/unit/documents/test_extraccion_base.py`

**Interfaces:**
- Produces: `extraer(pdf_bytes, *, schema: type[T], prompt: str, anio_esperado: int | None, client=None) -> tuple[T, str]` que centraliza: pre-flight `%PDF`, base64, llamada `messages.parse` con `MODELO`, `max_tokens=16000`, `output_config={"effort": "medium"}`, guard de `parsed_output is None`, y devuelve el modelo validado + el `doc_id`.
- `REGLAS_COMUNES: str` — el bloque de reglas que comparten todos los prompts (pesos completos, cifras en miles, confianza, datos-no-instrucciones).
- `f220.extraer_220` se reescribe encima de `extraer(...)` sin cambiar su firma pública ni su comportamiento: **los 28 tests del 220 deben seguir verdes sin tocarlos**.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_base_centraliza_preflight_y_llamada():
    with pytest.raises(ValueError, match="PDF"):
        extraer(b"no-es-pdf", schema=Extraccion220, prompt="x", anio_esperado=None,
                client=ClienteFalso(EXTRACCION))


def test_base_revienta_sin_salida_estructurada():
    with pytest.raises(ValueError, match="stop_reason"):
        extraer(b"%PDF-x", schema=Extraccion220, prompt="x", anio_esperado=None,
                client=ClienteSinSalida())


def test_reglas_comunes_traen_el_guard_de_instrucciones():
    assert "no instrucciones" in REGLAS_COMUNES or "datos a extraer" in REGLAS_COMUNES
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/documents/test_extraccion_base.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`_base.py` con lo descrito. Reescribir `extraer_220` para delegar, conservando sus guards propios (reconciliación contra el total, certificados, pensiones) **en el mismo orden actual**: certificados → año → reconciliación → pensiones.

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest tests/unit/documents -q`
Expected: los 28 del 220 verdes **sin haberlos modificado**, más los nuevos.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Extracción: base compartida (pre-flight, llamada, guards) y f220 encima

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Extractores de ingresos (4)

**Files:**
- Create: `src/declaras/extraccion/cert_pension.py`, `cert_bancario.py`, `cert_dividendos.py`, `cert_arriendo.py`
- Modify: `src/declaras/documents/parsers/certificados.py`, `src/declaras/documents/registry.py`
- Test: `tests/unit/documents/test_cert_ingresos.py`

**Interfaces:**
- Cada módulo produce su `Extraccion*` (pydantic, `extra="forbid"`, montos `ge=0`, `nit` con `pattern=r"^\d{7,10}$"`, `anio_gravable`, `confianza`) y un `extraer_*(pdf_bytes, anio_esperado=None, client=None)` que devuelve el modelo del Caso correspondiente:

| Módulo | Extrae | Devuelve | Guard propio |
|---|---|---|---|
| `cert_pension` | pagador, NIT, **12 mesadas**, retención, total pagado | `IngresoPension` | suma de mesadas reconcilia contra el total impreso (±1.000); exactamente 12 valores |
| `cert_bancario` | entidad, NIT, rendimientos, retención, **GMF pagado**, saldo a 31-dic | `tuple[Rendimiento, MontoDeclarado \| None]` (GMF va a beneficios) | rendimientos ≥ 0; si trae varias cuentas, suma y lo dice en la confianza |
| `cert_dividendos` | sociedad, NIT, **gravados vs no gravados**, retención, año de utilidades | `Dividendo` | gravados + no gravados reconcilia contra el total; si el certificado no discrimina → `ValueError` (no se puede liquidar sin la discriminación) |
| `cert_arriendo` | inmueble, contraparte + NIT, canon total, meses, retención, **costos** (predial, administración, comisión, reparaciones) | `Arriendo` | canon > 0; costos ≤ canon (si no, flag de revisión) |

- Cada prompt referencia **etiquetas**, no números de casilla, e incluye `REGLAS_COMUNES`.
- `certificados.py` gana `leer_pension`, `leer_bancario`, `leer_dividendos`, `leer_arriendo` que traducen a `DocumentReading` con `doc_type` `CERT_PENSION`, `CERT_BANCARIO`, `CERT_DIVIDENDOS`, `CERT_ARRIENDO`, y los cuatro entran al `LLM_READERS`.

- [ ] **Step 1: Escribir los tests que fallan**

Un test por extractor con cliente falso: mapeo campo por campo con **valores distintos por campo** (mata cruces), más el guard propio de cada uno (mesadas que no reconcilian, dividendos sin discriminar, etc.). Más un test de que los cuatro `doc_type` están en `registry.supported_types()`.

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/documents/test_cert_ingresos.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar los cuatro**

Sobre `_base.extraer`. Prompts con las etiquetas reales de cada certificado; para el de pensión, insistir en que las mesadas van **mes a mes** (la exención es mensual) y que un retroactivo va en el mes en que se pagó.

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest -q`
Expected: todo verde.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Extractores de ingresos: pensión, bancario, dividendos y arriendo

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Extractores de beneficios (5) y autodetección

**Files:**
- Create: `src/declaras/extraccion/cert_beneficio.py`, `src/declaras/documents/sniff.py`
- Modify: `src/declaras/documents/parsers/certificados.py`, `registry.py`, `src/declaras/api/routers/documents_read.py`
- Test: `tests/unit/documents/test_cert_beneficios.py`, `tests/unit/documents/test_sniff.py`

**Interfaces:**
- `cert_beneficio.py`: un solo módulo con `TipoBeneficio` (StrEnum: `PREPAGADA, INTERESES_VIVIENDA, ICETEX, AFC_FVP, DONACION_ESAL`), un `ExtraccionBeneficio{tipo, entidad, entidad_nit, valor, anio_gravable, certificada: bool, confianza}` y `extraer_beneficio(pdf, tipo=None, anio_esperado=None, client=None)`. Si `tipo is None` el modelo lo clasifica y lo reporta; si viene dado, se valida que coincida con lo que el modelo leyó (`ValueError` si no) — el hint no silencia la discrepancia.
- `sniff.py`: `detectar_tipo(pdf_bytes, client=None) -> str` que devuelve el `doc_type` del registry (portal o certificado) o `"DESCONOCIDO"`. Una sola llamada, prompt corto con la lista de tipos soportados.
- `documents_read.py`: el `POST /read` acepta `doc_type` **opcional**; si falta, llama a `detectar_tipo` y rutea. Si sale `DESCONOCIDO` → 422 pidiendo el tipo explícito.

- [ ] **Step 1: Escribir los tests que fallan**

- Un caso por tipo de beneficio (mapeo + año + confianza).
- `tipo` dado que **no** coincide con lo leído → `ValueError`.
- `detectar_tipo` devuelve `CERT_INGRESOS_220` para un fixture de 220 y `DESCONOCIDO` para basura.
- `POST /read` sin `doc_type` rutea; con `DESCONOCIDO` da 422.

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run pytest tests/unit/documents -q`
Expected: FAIL.

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Correr y ver el pass**

Run: `uv run pytest -q`
Expected: suite completa verde.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Extractores de beneficios y autodetección del tipo de documento

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Front — cliente de API y vistas de conciliación

**Files** (repo `~/Desktop/declaras/front`, rama `dev`):
- Modify: `src/consola/api.js`
- Create: `src/consola/Conciliacion.jsx`, `src/consola/Peticiones.jsx`, `src/consola/Ganancia.jsx`
- Modify: `src/consola/DetalleExpediente.jsx` (pestañas nuevas), `src/consola/consola.css`

**Interfaces:**
- Consumes: los endpoints de la Task 6. La consola **ya tiene** cliente de API (`request`, `ApiError`, `api.*`), hooks y componentes — esto es **aditivo**, no reescritura.
- Produces en `api.js`: `runConciliacion(caseId)`, `listPartidas(caseId)`, `resolverPartida(caseId, partidaId, payload)`, `listPeticiones(caseId)`, `postRespuesta(caseId, payload)`, `getLiquidacion(caseId)`, `cerrarPeticion(caseId, peticionId)`, y `uploadDocuments(caseId, files, peticionId?)` (multipart con varios archivos).
- `Ganancia.jsx`: la tarjeta de tres cifras — **según la DIAN / optimizada / te ahorras** — leyendo `{preliminar, actual, ganancia}`. Es la misma tarjeta que el prototipo ya tiene hardcodeada en `App.jsx` (`SavingsCard`): reutilizar su markup y CSS, cambiando la fuente de los números.
- `Peticiones.jsx`: lista priorizada. Cada ítem muestra `copy_sugerido` (con botón de copiar, porque en el MVP el contador lo manda a mano por WhatsApp), el `ahorro_estimado` como orientación, y zona de arrastrar-y-soltar **múltiples archivos**. Las de `pregunta_previa` se responden con Sí/No inline → `POST /respuestas`.
- `Conciliacion.jsx`: la mesa. Por partida, las dos versiones lado a lado (DIAN vs documento) con las dos diferencias (monto y retención), y los botones de decisión **filtrados por estado** (no ofrecer "usar documento" en una `SOLO_DIAN`).

- [ ] **Step 1: Extender `api.js`**

Seguir el patrón exacto de las funciones existentes (`request(path, options)`, helper `json(payload)`). Para la subida múltiple, un `FormData` con varios `files` y el `peticion_id` opcional.

- [ ] **Step 2: `Ganancia.jsx` y engancharlo en `DetalleExpediente`**

Verificación manual: con el back corriendo y un caso conciliado, la tarjeta muestra las tres cifras reales y `ganancia` coincide con `preliminar.impuesto − actual.impuesto`.

- [ ] **Step 3: `Peticiones.jsx`**

Verificación manual: responder "No" a prepagada hace desaparecer la petición sin recargar; soltar 2 archivos muestra el desenlace de cada uno.

- [ ] **Step 4: `Conciliacion.jsx`**

Verificación manual: una `SOLO_DIAN` **no** ofrece "usar documento"; resolver una discrepancia actualiza la ganancia en pantalla.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Consola: conciliación, peticiones y tarjeta de ganancia sobre el API real

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Recorrido completo del MVP, punta a punta

**Files:**
- Create: `docs/mvp-recorrido.md`
- Test: `tests/integration/test_recorrido_mvp.py`

**Interfaces:**
- Un test de integración que recorre **todo** con dobles (sin red, sin API key real): abrir caso → sembrar exógena de fixture → conciliar → preliminar → peticiones → subir 220 + prepagada + registro civil → resolver la discrepancia → liquidar → borrador. Afirma las cifras de cada etapa, incluida la ganancia.
- `docs/mvp-recorrido.md`: el guion para operar un caso real, con los comandos y qué se espera en cada paso — lo que el contador va a seguir en la primera sesión.

- [ ] **Step 1: Escribir el test del recorrido**

Debe afirmar, con números: el impuesto preliminar, el impuesto tras el 220, el impuesto final tras los beneficios, y que `ganancia` es la diferencia. Reusar los goldens donde apliquen (**1.495.977** y **5.418.627** son cifras ya verificadas del motor).

- [ ] **Step 2: Correr y ver el fallo, luego el pass**

Run: `uv run pytest tests/integration/test_recorrido_mvp.py -q`

- [ ] **Step 3: Escribir el guion de operación**

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Recorrido del MVP punta a punta: test de integración y guion de operación

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review del plan

1. **Cobertura del MVP**: fusión (T1) + duplicados (T2) + registry (T3) + conciliador incremental (T4-T5) + peticiones/liquidación/API (T6) + llave de cruce (T7) + extractores (T8-T10) + front (T11) + recorrido punta a punta (T12). El flujo acordado —DIAN → peticiones → documentos → conciliar → 210 con ganancia— queda cubierto de extremo a extremo.
2. **Orden de los extractores por lo que la DIAN NO puede traer**: T9 (ingresos) va antes que T10 (beneficios) porque cada certificado de ingreso aporta algo estructural que la exógena no tiene — las 12 mesadas (exención mensual), la discriminación de dividendos, los costos del arriendo, el GMF. Dentro de T10, dependientes y prepagada son los de mayor valor: son plata que la DIAN jamás verá.
3. **Riesgo mayor**: que la fusión rompa algo de Juan. Mitigación: T1 exige la suite de los dos lados verde antes de seguir; su código no se toca salvo `tax/uvt.py` (una vez) y los puntos de extensión que él dejó.
4. **Coordinación**: Juan sigue trabajando en `dev`. T1+T2 se empujan a `dev` el mismo día para que su ventana de divergencia sean minutos, no días. **Hay que avisarle antes del push.**
5. **El conversacional no está en el plan, y el MVP no lo necesita**: las peticiones y la incorporación de documentos son dos endpoints. En el MVP los consume la consola y el contador pregunta a mano; después los consume el agente sin cambiar el conciliador, el motor ni el render.
6. **Lo que queda fuera y está declarado**: agente conversacional, presentador MUISCA, pagos, casillas oficiales del 210, perfil independiente.
