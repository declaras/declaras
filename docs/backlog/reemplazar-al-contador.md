# Backlog: lo que falta para reemplazar al contador

Sale de la auditoría del 2026-07-29 contra las fuentes oficiales de la DIAN para el año gravable
2025, que quedó resumida en `docs/audits/2026-07-29-reemplazar-al-contador.md`. Lo que ya se hizo no
está acá; esto es lo que queda.

Cada ítem trae **por qué importa** (para poder despriorizarlo con criterio) y **qué toca hacer** (para
que arrancarlo no exija volver a investigar). El orden dentro de cada bloque es por plata en juego.

---

## Bloque 1 · Produce cifras equivocadas hoy

Estos no son "features que faltan": son casos en que el número que Clara muestra está mal. Van antes
de cualquier otra cosa porque un cliente que los sufre pierde plata o queda expuesto.

### 1.1 Residencia fiscal no se pregunta nunca

`Contribuyente.residente` existe con default `True` y nadie lo pregunta. El motor rechaza a los no
residentes (`motor/cierre.py`) pero solo cuando ya se armó el caso, y el onboarding no filtra.

Un no residente declara en el **formulario 110** por el sistema ordinario, no en el 210 (art. 10 ET;
la DIAN lo dice explícito en su abecé). Presentar en el formulario equivocado no es un error de
cifra: es una declaración que no cumple.

- Cuestionario de residencia en las preguntas fijas: 183 días continuos o discontinuos en 365, y las
  seis condiciones del numeral 3 (cónyuge o hijos residentes, 50% de ingresos, bienes o activos en el
  país, requerimiento sin acreditar, paraíso fiscal)
- No residente → fuera de alcance con explicación, no un error genérico
- Ojo con la excepción: el nacional que cumple el numeral 3 pero tiene 50% o más de activos o
  ingresos en su jurisdicción de domicilio NO es residente

### 1.2 Renta mundial y activos en el exterior

El residente tributa sobre renta y patrimonio de fuente mundial (art. 9 ET). La exógena solo ve
Colombia, así que un ingreso o una cuenta afuera son invisibles y nadie los pregunta.

Además hay una obligación **separada** de la declaración de renta: la declaración anual de activos en
el exterior (art. 607 ET) cuando el patrimonio afuera supera 2.000 UVT, con su propia sanción.

- Dos preguntas fijas: ¿ingresos del exterior? ¿activos en el exterior?
- Si los activos pasan 2.000 UVT, avisar de la obligación aparte
- Relevante para el segmento con cuentas en USD, cripto en exchanges extranjeros y trabajo remoto

### 1.3 Vehículos e inmuebles no se capturan

Sigue pendiente de antes. El patrimonio bruto define uno de los cinco topes de obligación (4.500 UVT,
$224.096.000 para AG2025), así que un patrimonio incompleto puede decirle a alguien que no está
obligado cuando sí lo está.

Medido en el caso real de prueba: la comparación con el borrador de la DIAN muestra $2.286.342 de
diferencia en la casilla 29, o sea patrimonio que la DIAN ve y el cálculo no.

- Captura guiada de vehículos e inmuebles con las reglas de valor patrimonial (arts. 267 y ss.)
- La exógena delata la compra de inmuebles y el reporte trae la **matrícula inmobiliaria** en la
  columna de información adicional: se puede detectar y preguntar, no esperar a que lo cuenten
- Las dos preguntas que quedaron abiertas: si el valor lo digita el contador o se lee del predial y
  de la tarjeta de propiedad; y si las deudas asociadas van con el bien o aparte

### 1.4 Aportes obligatorios que la DIAN tiene y el cálculo no

Encontrado por la comparación nueva en el caso real: casilla 33, la DIAN precargó $3.940.000 de
aportes de salud y pensión y nuestro formulario lleva 0. Es una deducción perdida.

La causa está identificada: la partida de aportes se resolvió con `CERRAR_SIN_SOPORTE` y el
certificado de salarios se contestó "no lo tengo", así que el hecho nunca entró.

- Cuando la DIAN precarga un INCRNGO que nosotros no tenemos, la comparación debería abrir una
  petición automática en vez de solo mostrar la diferencia
- Es el patrón general: **toda casilla donde declaramos menos que la DIAN debería poder convertirse
  en una acción**, no solo en una fila de tabla

---

## Bloque 2 · El último kilómetro

Sin esto Clara calcula y el cliente queda solo justo donde el contador remata. Es el bloque más
grande de construcción y el que define si el producto reemplaza al contador o solo lo ayuda.

### 2.1 Escribir el 210 en el portal y guardarlo como borrador

El *kill shot* del documento maestro. Hoy el adaptador **lee** (RUT, exógena, facturas, borrador
sugerido, declaración anterior) pero no **escribe**.

La Resolución 000227 del 23 de septiembre de 2025 juega a favor: eliminó la presentación litográfica,
así que todo el mundo pasa por el portal transaccional de todas formas.

- Flujo de escritura: diligenciar las casillas y guardar sin firmar (nivel 2, legalmente limpio)
- El nivel 3 (firmar por el usuario) sigue descartado: el instrumento de firma electrónica es
  personal e intransferible
- Los avisos técnicos de la DIAN son restricciones de diseño reales: sesión de 60 minutos, una sola
  pestaña activa, y descargar exógena **antes** que el reporte de factura electrónica

### 2.2 Recibo 490 y acompañamiento del pago

Una vez presentada, el pago va con el formulario 490. Hoy no existe nada.

- Generar el 490, enlazar a PSE, verificar que el pago quedó
- Las entidades autorizadas para recaudar están listadas en el micrositio, con qué canal soporta cada
  una (sucursal, PSE, tarjeta de crédito, corresponsal)
- Sin pago verificado, una declaración presentada y no pagada acumula intereses de mora (art. 634) y
  el cliente cree que terminó

### 2.3 Guía de firma electrónica

La firma electrónica es obligatoria y es el paso que más gente traba. La DIAN publica el paso a paso
de habilitar cuenta y de recuperarla, y el documento maestro ya contempla el patrón relevo (el agente
guía, el usuario reenvía los códigos).

- Detectar si el cliente ya tiene firma habilitada antes de llegar a Presentar
- Guía dentro del producto, no un enlace al portal
- Aviso importante de la DIAN: quien crea la cuenta por primera vez tiene la exógena y la sugerida
  disponibles **una semana después**, así que hay que empezar con anticipación

---

## Bloque 3 · Casos que hoy no se pueden atender

### 3.1 Ganancias ocasionales

La razón número uno por la que alguien con vida normal va al contador: vendió la casa o el carro,
recibió una herencia, ganó una rifa. Hoy `CASILLAS_SIN_MAPEAR` lo declara honestamente y el caso
simplemente no se puede cerrar.

- Módulo del motor para la cédula de ganancias ocasionales (casillas 112 a 120 y 127, ya nombradas)
- Tarifa del 15% (Ley 2277 de 2022), loterías y rifas al 20%
- Las exenciones grandes: 7.500 UVT de la casa de habitación (art. 311-1), 3.250 UVT en herencias
  (art. 307), y la regla de los dos años de posesión que define si es ganancia ocasional o renta
- Captura guiada, y aprovechar que la exógena delata la venta de inmuebles

### 3.2 Independiente con costos reales

Quedó delimitado al implementar `CLASIFICAR`: el freelancer sin empleados ya entra por rentas de
trabajo. Falta quien tenga costos reales que le convenga restar.

- Casillas 43 a 57, que ya están mapeadas
- La elección excluyente del art. 336 num. 4: restar costos **o** tomar la renta exenta del art. 206
  num. 10, no las dos. Es una optimización nueva en el motor
- Las tarifas de aporte del independiente son distintas a las del empleado (el código asume 4% + 4%
  de empleado en `peticiones.py`): un independiente aporta sobre un IBC del 40% del ingreso

### 3.3 Sucesiones ilíquidas

Se declaran en el 210 si el causante era residente (formulario 110 si no lo era), las presenta el
albacea o los herederos, y exigen que el representante tenga la responsabilidad 22 en su RUT.

- Detectar el caso al abrir el expediente y declararlo fuera de alcance con explicación
- El flujo completo es largo (ocho pasos en la guía de la DIAN, incluida una cita presencial que solo
  se oferta en Bogotá), así que probablemente no vale la pena antes de tener volumen

---

## Bloque 4 · Después de presentar

Territorio que nadie cubre, ni Clara ni el contador informal.

### 4.1 La exógena cambia después de que declaraste

La DIAN dice que la información exógena **se actualiza a mitad y al final de cada semana** durante la
temporada, porque los terceros corrigen lo que reportaron. Quien declaró temprano puede quedar
desalineado sin saberlo.

- Re-consultar la exógena después de presentar y avisar si cambió algo que mueva la cifra
- Es una razón de retención: el cliente sigue vinculado al producto después de pagar

### 4.2 Corrección de la declaración

- Arts. 588 y 589, según si la corrección aumenta el saldo a pagar o lo disminuye
- La sanción por corregir voluntariamente es del 10% del mayor valor (art. 644) si se hace antes de
  emplazamiento, y sube al 20% después: decirlo en pesos es lo que permite decidir

### 4.3 Beneficio de auditoría

Art. 689-3: si el impuesto neto de renta sube al menos 35% respecto del año anterior, la declaración
queda en firme en 6 meses; con 25%, en 12 meses. En vez de los 3 años normales del art. 714.

**Esto se puede calcular hoy**: ya se descarga la declaración del año anterior, así que el insumo
está. Es consejo de contador caro convertido en una línea de la pantalla.

### 4.4 Paquete de defensa

La firmeza general es de tres años (art. 714) y en ese tiempo la DIAN puede requerir. Clara ya tiene
todos los soportes y la memoria de cálculo con la norma de cada cifra.

- Un ZIP descargable con el 210, la memoria, los soportes y los avisos con los supuestos afirmados
- Es el reemplazo directo de "llame a su contador si la DIAN pregunta", y casi todo ya existe: es
  empaquetarlo

---

## Bloque 5 · Obligación y sanciones

### 5.1 Los dos topes que faltan en el chequeo de obligación

`OBLIGADO_DECLARAR` revisa ingresos, patrimonio, consignaciones y compras. Faltan:

- **Responsable de IVA a 31 de diciembre** (art. 592-2). El RUT que ya se descarga trae las
  responsabilidades: es leer un campo que ya está en el expediente
- **Consumos con tarjeta de crédito** separados de compras y consumos: son dos topes distintos de
  1.400 UVT cada uno en la lista de la DIAN, y hoy se comparan contra el mismo campo

### 5.2 Calculadora de sanción por extemporaneidad

Habilita el segundo producto del documento maestro (declaraciones atrasadas de años anteriores).

- 5% del impuesto por mes o fracción de retardo, tope 100% (art. 641)
- Sin impuesto a cargo: 0,5% de los ingresos brutos; sin ingresos: 1% del patrimonio líquido del año
  anterior
- Sanción mínima 10 UVT, **$524.000 en 2026** (art. 639). El calendario ya está implementado, así que
  el insumo de "cuántos meses de retardo" ya se puede calcular
- Intereses de mora del art. 634 aparte

### 5.3 Declaración voluntaria como producto de adquisición

Art. 6 par.: quien no está obligado pero tuvo retenciones puede declarar voluntariamente y la
declaración tiene plenos efectos legales. O sea que le devuelven la retención.

Hoy el motor emite `NO_OBLIGADO` y ahí muere. Calcular el saldo a favor y decirlo ("no estás
obligado, pero si declaras te devuelven $X") es un embudo de adquisición completo, no un aviso.

---

## Bloque 6 · Beneficios y UX menores

- **Beneficios del catálogo que la DIAN lista y Clara no pregunta**: vehículos eléctricos e híbridos,
  descuento por donación de alimentos, derechos de autor. Cada uno con su ahorro en pesos, como los
  demás
- **Certificación de dependientes**: la DIAN la exige "en los casos en que se requiera" y el panel ya
  marcó la debida diligencia como P0. Hoy el beneficio se afirma sin pedir soporte
- **La lista de documentos de "Agregar un certificado"** debería enumerar lo que la DIAN lista
  (escrituras, factura del vehículo, certificado de intereses, certificación de dependientes) en vez
  de ser un cajón genérico
- **"$ 0 te toca pagar"** en la etapa Resultado parece un error de cálculo aunque sea correcto.
  Debería decir "no te toca pagar nada" y explicar por qué
- **La pestaña "Ingresos"** del borrador abre con patrimonio (casilla 29), que no es un ingreso. O se
  renombra o el patrimonio va a su propio bloque
- **Los nombres de las decisiones** (`decisiones.js`) siguen en el front, así que agregar un valor al
  enum `Decision` del backend lo muestra en crudo sin que nada avise. Es el mismo modo de falla que
  `parametros/en_palabras.py` cerró para los pasos del cálculo y los renglones del 210

---

## Deuda técnica que toca de cerca

- **Sin migraciones**: cada columna nueva exige borrar la base. Esta sesión agregó
  `IngresoLaboral.promedio_mensual_6m` y la anterior el campo `clase` de la resolución
- **Los mensajes de los avisos se persisten como texto** junto a cada versión de la liquidación, así
  que mejorar una redacción no alcanza a lo ya guardado. Es defendible (un aviso es un hecho fechado)
  pero hay que saberlo antes de tener clientes reales
- **`auth.py` al 26% de cobertura**, y es justo la pieza que gasta intentos de login: la DIAN bloquea
  al tercero
- **El conector de Playwright, 1.281 líneas muertas** desde que existe el conector HTTP
- **Los documentos se guardan sin cifrar y sin política de retención**, y son cédulas, direcciones y
  patrimonios completos
- **El bug de concurrencia de Esteban** (`test_cerrar_y_resolver_a_la_vez...`) falla 4 de 20 corridas.
  Sigue sin tocar a propósito
