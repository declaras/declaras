# Spec de diseño — Demo declaras (documentos → 210)

**Fecha**: 2026-07-25
**Estado**: aprobado en diseño, pendiente plan de implementación
**Contexto**: rebanada vertical del producto Clara (ver `CLARA_documento_maestro.pdf`, 2026-07-21). El demo existe para operar con un contador en el loop: recibe los hechos de un contribuyente (documentos + entrada manual), calcula y muestra el Formulario 210 del año gravable 2025 con trazabilidad total, casilla por casilla.

---

## 1. Objetivo y no-objetivos

**Objetivo**: dado un caso de contribuyente (sintético o real), producir el borrador del Formulario 210 AG 2025 correcto, auditable casilla por casilla por un contador, para tres escenarios:

- **E1 — Asalariado**: un empleador, beneficios típicos (dependientes, prepagada, intereses de vivienda, AFC, GMF, 1% facturas).
- **E2 — Asalariado + pensión + movimientos**: agrega mesada pensional y rendimientos financieros.
- **E3 — Asalariado + renta de capital + dividendos**: agrega arriendos (con costos procedentes) y dividendos gravados/no gravados.

El perfil "fácil sin movimientos" (solo patrimonio) sale gratis como caso degenerado de cualquier escenario.

**No-objetivos del demo** (viven en Clara, no aquí): orquestador WhatsApp, conector DIAN de lectura (browser agent), presentador en Muisca, mini web de claves, pagos, bóveda de soportes, perfiles independiente/honorarios, ganancias ocasionales (venta de activos, herencias), protocolo de ingreso exógena ajeno, asignación de dependientes entre cónyuges (requiere optimizar dos casos a la vez).

**Principio rector de reutilización**: todo lo que se construye es el "camino manual" permanente de Clara (sección 3.2 del doc maestro: el cliente que no da la clave DIAN sube sus PDFs). Cuando lleguen el conector DIAN y el chat, serán *extractores/clientes nuevos escribiendo al mismo Caso Tributario*. Nada del demo se desecha.

---

## 2. Arquitectura

```
ENTRADA (web mínima, repo front)
  subir documentos + entrada manual de hechos
        │
  [EXTRACCIÓN DOCUMENTAL]  ← único punto con LLM del sistema
  PDF/foto → JSON tipado por documento
        │
        ▼
  ╔═══ CASO TRIBUTARIO ═══╗   contrato pydantic canónico
  ║ hechos, nunca         ║   cada dato con proveniencia
  ║ conclusiones          ║   (fuente, doc, página, confianza)
  ╚═══════╦═══════════════╝
          │        [PARÁMETROS AG 2025] (config versionada por año)
          ▼        ▼
  [MOTOR TRIBUTARIO + OPTIMIZADOR]   ← determinístico, cero LLM
          │
          ▼
  LIQUIDACIÓN = casillas del 210, cada una con fórmula,
  insumos, soporte de origen y ahorro logrado
          │
          ▼
  SALIDA: vista casilla por casilla + borrador 210 PDF + memoria de cálculo
```

Reglas de arquitectura (heredadas del doc maestro, el código debe hacerlas imposibles de violar):

1. **La IA conversa/extrae, el motor calcula.** Ningún número del 210 sale de un LLM.
2. **Hechos vs conclusiones.** El Caso solo contiene hechos observados; toda cifra derivada la produce el motor.
3. **UVT por año gravable, nunca "la UVT actual".** Parámetros en config versionada; toda regla referencia el año.
4. **Trazabilidad total.** Cada casilla es un nodo de un árbol de cálculo: fórmula + insumos + proveniencia.
5. **El camino manual nunca se bloquea.** Cualquier hecho puede entrar/corregirse a mano; un extractor caído no frena un caso.

---

## 3. Caso Tributario (contrato canónico)

Estructura pydantic, modular por tipo de ingreso. Contiene **hechos, nunca conclusiones**.

```
CasoTributario
├── año_gravable: 2025
├── contribuyente: {tipo_doc, num_doc, nombre, residente: bool,
│                   fecha_límite (por dígitos de cédula)}
├── ingresos
│   ├── laborales[]:   {empleador (nit, nombre), salarios, cesantías_e_intereses,
│   │                   prima, bonificaciones, aportes_obligatorios_salud,
│   │                   aportes_obligatorios_pension + fondo_solidaridad,
│   │                   retencion_practicada}
│   ├── pensiones[]:   {pagador, mesada_por_mes: [12 valores], retencion}
│   │                   → mensual porque la exención de 1.000 UVT es POR MES
│   ├── rendimientos[]: {entidad, valor_intereses, retencion}
│   ├── arriendos[]:   {inmueble_ref, canon_total_año, meses_arrendado,
│   │                   retenciones, costos: {predial, administracion,
│   │                   comision_inmobiliaria, reparaciones[]} — todo con soporte}
│   └── dividendos[]:  {sociedad (nit, nombre), no_gravados, gravados,
│                       retencion, año_utilidades}
├── beneficios_declarables
│   ├── dependientes[]: {tipo (hijo<18 | hijo 18-23 estudiante | hijo>23 discapacidad
│   │                   | cónyuge sin ingresos | padre/hermano), meses_de_derecho, soporte}
│   ├── medicina_prepagada: {pagos_por_mes o total_año}
│   ├── intereses_vivienda: {entidad, valor_año}
│   ├── intereses_icetex: {valor_año}
│   ├── aportes_afc_fvp[]: {entidad, tipo (AFC|FVP), valor}
│   ├── gmf_pagado: {total_certificado}
│   ├── facturas_electronicas_total: {valor_compras}   → para el 1%
│   └── donaciones_esal[]: {entidad, valor, certificada: bool}
├── patrimonio
│   ├── activos[]: {tipo (inmueble|vehículo|cuenta|inversión|otro),
│   │               descripción, valor_31dic, costo_fiscal}
│   ├── deudas[]:  {acreedor, saldo_31dic, soporte}
│   └── patrimonio_liquido_año_anterior   → para comparación patrimonial
└── creditos_y_arrastres
    ├── retenciones ya vienen por ingreso (arriba)
    ├── anticipo_pagado_año_anterior
    ├── saldo_a_favor_año_anterior
    └── es_primer_año_declarando: bool     → afecta % del anticipo
```

**Proveniencia**: todo valor hoja se envuelve en `Hecho[T] = {valor: T, fuente: Fuente}` donde `Fuente = Documento(tipo, doc_id, página, confianza) | Manual(quien, cuando) | Fixture(nombre)`. Cuando llegue el conector DIAN se agrega `Exogena(formato, renglón)` sin tocar nada más.

**Completitud**: el schema declara qué campos requiere cada escenario; "qué falta" es un recorrido de huecos sobre el Caso (base del futuro gap analysis conversacional).

---

## 4. Parámetros AG 2025 (config versionada, nunca constantes en código)

| Parámetro | Valor AG 2025 |
|---|---|
| UVT | $49.799 (UVT 2026 = $52.374, solo para sanciones pagadas en 2026) |
| Topes obligación (ingresos / compras / consignaciones) | 1.400 UVT = $69.718.600 |
| Tope obligación patrimonio bruto | 4.500 UVT = $224.095.500 |
| Límite global exentas+deducciones cédula general | menor entre 40% de ingresos netos y 1.340 UVT = $66.730.660 |
| Renta exenta 25% laboral (tope anual) | 790 UVT = $39.341.210 |
| Dependientes 72 UVT c/u (máx 4) — extra-límite | 288 UVT máx = $14.342.112 |
| Dependientes art. 387 (alternativa) | 10% del ingreso, máx 32 UVT/mes |
| Medicina prepagada | 16 UVT/mes → 192 UVT/año = $9.561.408 |
| Intereses vivienda | 1.200 UVT/año = $59.758.800 |
| Intereses Icetex | 100 UVT/año = $4.979.900 |
| AFC + FVP | ≤30% del ingreso, tope 3.800 UVT = $189.236.200 |
| GMF | 50% de lo pagado, sin tope propio |
| 1% compras con factura electrónica — extra-límite | tope 240 UVT = $11.951.760 |
| Exención pensional | 1.000 UVT **por mes y por contribuyente** (I-2: agregando pagadores; art. 206-5) |
| Umbral tabla 241 / descuento dividendos | 1.090 UVT = $54.280.910 |
| Tabla art. 241 | marginales 0/19/28/33/35/37/39% |
| Tarifa dividendos gravados (1er componente) | 35% |
| Descuento art. 254-1 | 19% marginal sobre cédula dividendos que exceda 1.090 UVT |
| Componente inflacionario rendimientos | **PENDIENTE decreto AG 2025** (AG 2024 fue 50,88%, Decreto 771/2025). Bloquea exactitud de E2/E3; parametrizado desde el día uno |
| Sanción mínima (pagada en 2026) | 10 UVT 2026 = $523.740 |
| Anticipo renta | 25% (1er año) / 50% (2º) / 75% (3º+) sobre impuesto neto o promedio de dos años, menos retenciones — a elección del contribuyente la base |

---

## 5. Motor tributario

Función pura: `liquidar(caso, parametros, elecciones) → Liquidacion`.

**Depuración cédula general** (art. 336, orden estricto — apéndice A.3 del doc maestro):

1. Sumar ingresos (laborales + rendimientos + arriendos; dividendos y pensiones van en cédulas propias).
2. Restar INCRNGO: aportes obligatorios salud/pensión/solidaridad; componente inflacionario de rendimientos.
3. Restar costos procedentes de arriendos (predial, administración, comisión, reparaciones con soporte) — costos, no sujetos al 40%.
4. Restar rentas exentas y deducciones especiales, **sin exceder** el menor entre 40% del subtotal y 1.340 UVT. Dentro del límite: intereses vivienda, prepagada, art. 387 (10%), AFC/FVP, Icetex, GMF.
5. Renta exenta 25% laboral (tope 790 UVT): se calcula sobre ingresos laborales depurados (brutos − INCR − deducciones art. 387); las deducciones de numerales 3 y 5 del 336 no integran esa base. **Sí integra el límite del 40%.**
6. Extra-límite (no cuentan para el 40%): 72 UVT por dependiente (máx 4) y 1% de compras con factura electrónica (máx 240 UVT).
7. Renta líquida gravable → tabla art. 241.

**Cédula de pensiones**: por cada mes, exento hasta 1.000 UVT del agregado de mesadas del contribuyente (I-2: el tope es del contribuyente, se suman los pagadores del mes antes de restar); el exceso anualizado es renta gravable que se suma a la base de la tabla 241. Sin acceso a las deducciones del 40%.

**Cédula de dividendos**:
- *No gravados*: se suman a la base de tabla 241 (junto con general + pensiones). Luego descuento art. 254-1 = 19% marginal sobre la porción de dividendos que exceda 1.090 UVT (resta del impuesto, no de la base).
- *Gravados*: primer componente al 35%; el neto restante se suma a la base de tabla 241 y participa también del descuento 254-1.

**Créditos y cierre**: impuesto según tabla(s) − descuentos (254-1, 25% donaciones ESAL certificadas) − retenciones (todas las fuentes) − anticipo pagado − saldo a favor anterior + anticipo año siguiente → saldo a pagar / a favor.

**Validaciones pre-cierre** (bloqueantes o con flag al contador):
- **Comparación patrimonial** (A.6): si renta gravable + exentas + INCR < incremento patrimonial líquido del año (ajustado por impuesto pagado), diferencia = renta gravable salvo justificación → flag.
- Chequeo de topes de obligación (¿debía declarar?).
- Consistencia aritmética de casillas (el render nunca corrige, solo refleja).

**Salida — Liquidación**: árbol de cálculo. Cada casilla del 210 = `{codigo_casilla, valor, formula, insumos: [refs a Hechos u otras casillas], regla: ref normativa}`. De ahí salen la vista de auditoría, la memoria de cálculo y el PDF.

> **Pendiente de implementación**: mapear el árbol contra la numeración oficial de casillas del Formulario 210 (formato DIAN vigente para AG 2025 y su instructivo). El motor calcula por concepto; el mapeo a números de casilla es una tabla aparte.

---

## 6. Optimizador

Búsqueda exhaustiva sobre el espacio de elecciones legales; el motor evalúa cada combinación. **Determinístico**: mismo caso + mismos parámetros → mismas elecciones → mismo 210.

**Elecciones del demo**:
- **Dependientes**: 72 UVT (extra-límite) vs 10% art. 387 (dentro del límite) — combinables según convenga.
- **Composición del 40%**: qué beneficios entran si el límite se copa; prioridad a lo "gratis" (gastos ya hechos: prepagada, intereses, GMF), AFC de último (compromete liquidez — truco A.9.2). Nota: el 25% laboral interactúa con las demás deducciones (se calcula después de restarlas); el optimizador captura ese efecto automáticamente al evaluar combinaciones completas.
- **Base del anticipo**: impuesto del año vs promedio de dos años (el menor).

**Reglas duras**:
- El optimizador **jamás inventa hechos** — solo elige entre tratamientos legales de hechos presentes en el Caso.
- **Desempate explícito**: a igual impuesto, gana la combinación que (1) exige menos documentos de soporte, luego (2) compromete menos liquidez. Determinista por diseño, no por orden de enumeración.
- Toda combinación pasa por las validaciones del motor; una elección que viole un tope es inválida, no "cara".

**Ahorro marginal** (reutilizable por el futuro gap analysis F5): `ahorro(hecho_hipotetico) = impuesto(caso) − impuesto(caso + hecho)`. En el demo alimenta la columna "ahorro logrado" por casilla.

**Propiedades de test**: el resultado optimizado nunca es peor que la liquidación ingenua (sin elecciones); todas las salidas respetan topes; idempotencia.

---

## 7. Extracción documental y entrada manual

**v1 (demo)**: extractor LLM solo para el **certificado de ingresos y retenciones (220)** → `Hecho`s de ingresos laborales. Schema de salida tipado, validación pydantic, confianza por campo.

**v1.1**: certificado de dividendos, certificado bancario (rendimientos + GMF), certificado del fondo de pensiones, certificado de inmobiliaria.

**Diferido**: exógena (los hechos que aportaría entran por fixtures o manual; los golden cases los incluyen como si la exógena los hubiera traído, para que el extractor futuro solo reemplace la forma de llenar campos existentes), y los documentos de beneficios (prepagada, intereses, registro civil de dependientes) que en el demo entran manuales.

**Entrada manual**: la pantalla de revisión permite crear, corregir y eliminar cualquier `Hecho` del Caso (con fuente `Manual`). Es la misma pantalla del contador-revisor y el fallback universal de extracción.

---

## 8. Golden cases (fixtures sintéticos, verificables a mano)

| Caso | Escenario | Qué ejercita |
|---|---|---|
| **G0** | Fácil sin movimientos | Obligado por patrimonio, ingreso ~0; declaración patrimonial; impuesto $0; comparación patrimonial limpia |
| **G1** | Asalariado | 220 completo; dependientes (72 vs 10%); prepagada/intereses/GMF/1%; límite 40% copado — el optimizador debe decidir |
| **G2** | Asalariado + pensión + movimientos | Mesada bajo y sobre 1.000 UVT/mes (dos variantes); rendimientos con componente inflacionario; consignaciones que obligan sin ser ingreso |
| **G3** | Asalariado + capital + dividendos | Arriendos con costos procedentes; dividendos gravados y no gravados; descuento 254-1; retenciones múltiples |

Cada golden case tiene su 210 esperado calculado a mano (y validado por el contador cuando se contrate). Los casos reales (propios o con consentimiento explícito) entran después como casos de regresión — **nunca datos de clientes de HabiCapital**.

---

## 9. Estructura de repos

- **`back`** (este repo, Python 3.13 + FastAPI + pydantic + pytest):
  ```
  src/declaras/
  ├── caso/          # schema CasoTributario + Hecho/Fuente
  ├── parametros/    # ag2025.yaml + loader versionado
  ├── motor/         # cédulas, límites, tabla 241, validaciones
  ├── optimizador/   # elecciones, búsqueda, desempate, ahorro marginal
  ├── render/        # árbol → casillas 210 → vista/PDF/memoria
  ├── extraccion/    # extractor 220 (LLM) + contratos por documento
  └── api/           # FastAPI: casos CRUD, upload, liquidar
  tests/golden/      # G0–G3 con 210 esperado
  ```
- **`front`**: web mínima — crear caso, subir documentos, pantalla de revisión de hechos (editar/agregar), vista de liquidación casilla por casilla, descarga del borrador.

---

## 10. Riesgos y pendientes

| # | Pendiente | Impacto |
|---|---|---|
| 1 | Decreto componente inflacionario AG 2025 (sale ~mitad 2026) | Exactitud de E2/E3; parametrizado, un cambio de config |
| 2 | Mapeo a casillas oficiales del 210 AG 2025 + instructivo DIAN | Render final; el cálculo por concepto no depende de esto |
| 3 | Calendario oficial (decreto de plazos) para `fecha_límite` | Solo informativo en el demo |
| 4 | Precisión del extractor 220 sobre formatos reales de empleadores | Mitigado por entrada manual + revisión del contador |
| 5 | Interpretaciones finas (base exacta del 25%, interacción 387/336) | Validar con el contador contra los golden cases; el árbol de cálculo hace visible cada interpretación |

**Actualizar el doc maestro de Clara** (hallazgos de este diseño): falta el perfil pensionado en la sección 3.4 (trigger, certificado del fondo, exención mensual de 1.000 UVT) y falta el componente inflacionario en el apéndice A.4.
