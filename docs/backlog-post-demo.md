# Backlog post-demo — triage de la review final de rama

**Fecha**: 2026-07-26 · **Fuente**: review final de `feature/demo-motor` (43 commits, 189 tests) + ledger del plan `docs/plans/2026-07-25-demo-declaras.md`.

## Pre-demo (manual, no bloquea merge)

- [ ] **Eval del extractor con un 220 real** (`uv run python scripts/probar_extractor.py <pdf> [año]`, requiere `ANTHROPIC_API_KEY`).
- [ ] **Eval con un 220 mixto salario+pensión**: la defensa contra el plegado de pensiones en `bonificaciones` vive en el prompt y solo un PDF real la verifica.
- [ ] Smoke del upload por el API con llave real (sin llave el endpoint responde 503 accionable).

## Para la primera sesión con el contador

Validar las **interpretaciones registradas** contra los goldens (cada una está comentada en el código y en spec/plan):

| ID | Interpretación | Dónde |
|---|---|---|
| I-1 | Base del 25% exento: bruto laboral − INCR aportes − deducciones imputables al trabajo (GMF fuera; AFC y 387 dentro) | `motor/general.py` |
| I-2 | Exención pensional: 1.000 UVT **por mes y por contribuyente** (agregando pagadores) | `motor/pensiones.py` |
| I-3 | Descuento 254-1 sobre dividendos gravados **netos** del 35% (art. 242 inc. 2) | `motor/impuesto.py` |
| I-4 | "Impuesto a cargo" del tope art. 258 = IMPUESTO_241 + IMP_DIV_35 (antes de descuentos) | `motor/cierre.py` |
| I-5 | Comparación patrimonial: la pensión justifica **bruta** (exenta+gravada), art. 236 | `motor/cierre.py` |

Extras a validar: base del 30% AFC = bruto general (no solo laboral); pensiones del exterior reciben la exención (Ley 2277 — hoy el schema no distingue origen); tratamiento conservador de cesantías (se suman al bruto laboral sin exención propia del art. 206-4).

## Fix wave 2 (código, cuando se decida con el front)

- Flag `CONFIANZA_BAJA` ya persiste en la liquidación; decidir la UX (el umbral compartido vive en `caso/fuentes.py::CONFIANZA_MINIMA`).
- Extracción fuera del candado del upload (hoy un PUT espera detrás de una extracción LLM — un front con autosave se congela).
- `GET /documentos/{doc_id}` para servir los PDFs persistidos.
- Tipar `Casilla` (hoy `list[dict]` → OpenAPI flojo) y documentar el 409 del upload.
- Detector doble-220 mejorado: mismo certificado re-escaneado (bytes distintos) hoy solo lo ataja el flag `EMPLEADOR_DUPLICADO`.

## Deuda registrada (post-demo, con contexto en git history)

- **Proveniencia al render**: `Nodo` no lleva `fuente` — la trazabilidad de casilla es fórmula+insumos+norma (2 de 3 patas del spec). Requiere diseño (qué fuente lleva un nodo agregado).
- **Columna "ahorro logrado"**: `optimizador.ahorro_marginal` ya alimenta `Peticion.ahorro_estimado` (T6) y `GET /liquidacion` devuelve la ganancia real; falta conectarlo al render. ⚠️ Los ahorros marginales NO son aditivos (sumar contra el mismo base sobreestima hasta 64% con el cap copado): la UI debe acumular, y `Peticion.ahorro_es_techo` distingue lo medido del tope legal de un beneficio (no se pueden presentar como el mismo número).
- **Gap analysis de completitud** (spec §3): recorrido de huecos del Caso por escenario — no implementado.
- **Prorrateo de dependientes**: `Dependiente.meses` se captura pero el motor otorga 72 UVT/387 anualizados (flag `DEPENDIENTE_PARCIAL` lo advierte).
- **Proveniencia despareja**: `Creditos` y `patrimonio_liquido_anterior` son ints sin `Fuente` — decidir granularidad una sola vez.
- **Optimizador**: ~~ignora flags `bloqueante`~~ **CERRADO en T6**. `bloqueante` ya bloquea: `optimizar(caso, p, flags_previos=...)` se niega si hay un aviso bloqueante (del motor o del conciliador), `services/conciliacion/liquidaciones.py::liquidar_conciliado` fusiona `conciliacion.avisos()` en `Liquidacion.flags` y liquida con las elecciones **por defecto** cuando hay uno vivo (el óptimo de una base incompleta puede ser la elección equivocada para el 210 completo), y el servicio se niega a marcar el borrador listo. Lo que sigue abierto del optimizador: el desempate por "elecciones activas" es proxy de "(1) menos documentos (2) menos liquidez" — diverge cuando entren elecciones tipo AFC; término `activas` inerte con 2 flags.
- **Mapeo a casillas oficiales DIAN** + formateo por tipo de casilla (pendiente #2 del spec; el borrador se declara "por conceptos").
- **Parámetros**: `componente_inflacionario` AG 2025 pendiente de decreto (flag automático mientras tanto); al crear `ag2026.yaml` verificar coherencia constante↔tarifas de la tabla.
- Anclas de test faltantes (declaradas): `porcentaje()` en general.py sin caso de empate `,50`; `encoding=` sin ancla; fixture orden certificados-vs-año del extractor.
- Menores acumulados: schema sin `fecha_limite`/`costo_fiscal`/`año_utilidades`/`meses_arrendado`; mesada 13 y retroactivos sin representación; NIT sin checksum (laxitud 7 dígitos deliberada); `Liquidacion.valor` KeyError crudo; Nodo/Flag no frozen; separador de miles `1,000` en trazas en español; no-monotonicidad de la fórmula de ley en bordes 8.670/31.000 UVT (propiedad de la ley, documentada).

## Fuera de este repo

- **Plan del front** (`declaras-front`): pantalla de casos, upload, revisión de hechos (PUT completo), casillas con `valor_texto`, flags con severidad.
- **Doc maestro de Clara**: devolverle los hallazgos — falta el perfil pensionado en §3.4 y el componente inflacionario en el apéndice A.4.
