# Auditoría: qué le falta a Clara para reemplazar al contador

Fecha: 2026-07-29. Fuentes verificadas ese día contra el micrositio oficial de la DIAN para el año
gravable 2025 (micrositios.dian.gov.co/renta-personas-naturales-ag-2025) y las normas que ese
micrositio cita: Ley 2277 de 2022, Estatuto Tributario, Decreto 1625 de 2016, Decreto 2229 de 2023
(calendario permanente), Resolución 000193 de 2024 (UVT 2025 = $49.799) y Resolución 000227 del 23
de septiembre de 2025 (toda declaración se presenta por el portal transaccional; se eliminó el
medio litográfico).

## Cifras oficiales AG2025 confirmadas contra la DIAN

| Concepto | Valor | Estado en el código |
|---|---|---|
| UVT 2025 | $49.799 | correcto (`ag2025.yaml`) |
| UVT 2026 | $52.374 | correcto (`uvt_siguiente`) |
| Tope ingresos | 1.400 UVT ($69.719.000) | correcto |
| Tope patrimonio | 4.500 UVT ($224.096.000) | correcto |
| Topes consignaciones / compras / TC | 1.400 UVT c/u | parcial (ver abajo) |
| Límite 40% / 1.340 UVT (art. 336) | correcto | correcto |
| 25% exenta / 790 UVT (art. 206-10) | correcto | correcto |
| Dependientes 72 UVT × máx. 4 | correcto | correcto |
| 1% facturas / 240 UVT | correcto | correcto |
| Tabla art. 241 (0/19/28/33/35/37/39) | correcta | correcta |
| Pensiones exentas 1.000 UVT/mes (206-5) | correcto | correcto |
| Anticipo 25/50/75 con promedio (art. 807) | correcto | correcto |
| GMF 50% deducible (art. 115) | correcto | correcto |
| Sanción mínima 2026 | $524.000 (10 UVT) | NO existe en el producto |
| Componente inflacionario AG2025 | decreto pendiente | `null` con flag provisional (correcto) |

El resto del contenido está en la conversación del 2026-07-29 y se irá convirtiendo en specs
individuales en la wiki según se prioricen los ítems.
