# ADR 0005: dos familias de lectores de documentos, un solo servicio

Fecha: 2026-07-25. Estado: aceptado.

## Contexto

Clara necesita leer documentos de dos naturalezas muy distintas: los que entrega el
portal de la DIAN (XLSX y PDF con estructura fija y estable) y los que el cliente manda
por WhatsApp (fotos y PDFs heterogeneos, un formato distinto por cada banco o entidad).
Se pregunto si conviene un lector generico con IA para todo, o uno especializado por tipo.

## Decision

Un solo servicio (`DocumentReaderService`), con un registro de lectores despachado por
tipo de documento (`DETERMINISTIC_READERS`). Los documentos del portal se leen con
parsers deterministicos (cero IA); los del cliente se leeran con un modelo de vision
cuando se implemente ese lector (no en este alcance). Ambos producen el mismo modelo
uniforme (`DocumentReading`): campos con su valor, confianza y procedencia (celda,
casilla o fragmento de origen).

El tipo de documento se recibe **por parametro**, no se adivina: en el flujo del producto
el agente siempre sabe que pidio (el gap analysis dispara la pregunta y el tipo esperado
juntos). La deteccion automatica queda para cuando el cliente manda algo que nadie le
pidio, y es un modulo aparte que no se ha construido todavia.

## Por que no meterle un VLM a los documentos del portal

Pagar tokens por leer una celda de una hoja de calculo es desperdiciar dinero y, peor,
cambiar un resultado cien por ciento reproducible por uno probabilistico donde no hace
falta. La exogena y el resumen de facturas se leen con `openpyxl` sobre celdas conocidas;
el RUT (que no tiene campos de formulario) se lee reconstruyendo el orden visual del
texto, sin IA. La confianza de cada lector queda declarada en el propio dato: los
parsers de XLSX reportan certeza total (`Confidence.DETERMINISTIC`); el del RUT, al ser
posicional sobre un PDF sin estructura, reporta confianza baja (`Confidence.LOW`) y deja
el texto completo disponible para que un contador lo revise si algo no cuadra.

## El hallazgo que hizo posible el parser de exogena

El XLSX de exogena trae, para cada valor reportado por un tercero, la columna "Uso
declaracion Sugerida" con el renglon exacto del formulario 210 al que va ese valor (por
ejemplo "R36 Otras rentas exentas") y el tope de obligacion al que cuenta. La DIAN ya
resuelve el mapeo mas dificil del motor tributario; el parser solo tiene que leerlo.

## Como se resolvio el RUT sin campos de formulario

El PDF que entrega el portal no tiene `AcroForm`: cada digito de un numero se dibuja en
su propia caja, y la extraccion de texto por posicion (x, y) de pypdf no es confiable en
corridas de texto continuas. Lo que si es estable, verificado contra un RUT real: el
generador dibuja primero toda la plantilla (etiquetas y secciones) como un bloque de
texto enorme, y despues, en el mismo orden visual del formulario, dibuja cada valor
diligenciado como un fragmento aparte. El parser localiza el final de ese bloque y lee
los valores con un cursor de busqueda acotada (`_Cursor.take_matching`): busca, dentro de
una ventana corta, el siguiente fragmento cuya forma coincide con lo esperado (solo
digitos, solo letras, fecha de 8 digitos), en vez de asumir una posicion fija. Eso lo hace
tolerante a que algunos campos opcionales del formulario vengan vacios.

## Sincronico, no por jobs

A diferencia de la extraccion en el portal DIAN (que tarda minutos y usa jobs
asincronos), leer un documento ya descargado toma segundos. Meterle una cola de jobs
aqui seria complejidad sin beneficio. Hay cache por hash de contenido y tipo, para no
releer (ni pagarle dos veces a un modelo de vision, cuando exista) el mismo archivo.

## Dos entradas a la misma operacion

`POST /v1/documents/read` recibe bytes directos (multipart): es lo que llama el agente
cuando el cliente manda una foto. `POST /v1/documents/read-stored` recibe la referencia
de un documento que el conector DIAN ya descargo (`storage_uri`): encadena la extraccion
del portal con la lectura estructurada sin bajar el archivo dos veces.

## Pruebas sin datos reales

Los archivos reales de un contribuyente no se versionan ni se usan en pruebas: traen
datos personales y financieros reales. `tests/documents_fixtures.py` construye XLSX y PDF
sinteticos que reproducen la misma estructura (calibrada contra los documentos reales)
con datos inventados. El fixture del RUT reproduce ademas el detalle que hizo fallar el
primer intento: la plantilla debe dibujarse en un solo fragmento de texto, no envuelta en
varias lineas, para que el detector de "fin de la plantilla" se comporte igual que con el
documento real.

## Pendiente

- Lector con modelo de vision para documentos del cliente (certificados de banco, cédula,
  registro civil), con el mismo modelo `DocumentReading` de salida.
- Deteccion automatica de tipo cuando el cliente manda un documento sin que se le pidiera.
- Parser del formulario 210 en PDF (declaracion anterior y sugerida), que probablemente
  comparte el mismo problema de PDF sin campos de formulario que el RUT.
