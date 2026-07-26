# ADR 0006: el expediente como agregado central

Fecha: 2026-07-25. Estado: aceptado.

## Contexto

El conector DIAN produce jobs de extraccion sueltos; el servicio de documentos lee
archivos sueltos. Ninguno de los dos sabe que existe un cliente, ni organiza su trabajo
por caso. Sin ese amarre no hay de donde construir la consola del contador (que necesita
ver, por cliente, sus documentos, lo que se leyo de cada uno y que falta revisar), ni
un lugar natural donde el futuro motor tributario deposite su borrador.

## Decision

Un agregado nuevo, `Case` (expediente), con cinco piezas:

- **Client**: el contribuyente. Persiste entre anios gravables.
- **Case**: un expediente, uno por cliente y anio gravable (restriccion unica en la
  base de datos, no solo en el codigo).
- **CaseDocument**: un documento del expediente, con su origen (portal DIAN o subido por
  el cliente) y su lectura estructurada si ya se proceso.
- **CaseFlag**: algo que un contador debe revisar antes de dar el expediente por bueno.
- **CaseEvent**: bitacora de auditoria, append-only, de todo lo que le paso al expediente.

Persistencia en tablas normalizadas (`clients`, `cases`, `case_documents`, `case_flags`,
`case_events`), no un blob JSON: la consola necesita listar y filtrar (todos los
expedientes de un cliente, los flags abiertos de un caso) de forma barata, y eso pide
tablas relacionales, no deserializar un documento grande en cada consulta.

Un servicio nuevo, `CaseService`, es el que amarra los otros dos modulos al expediente:

- `link_extraction_result`: toma un job de extraccion DIAN ya terminado y, por cada
  documento bajado, lo registra en el expediente, lo lee con el servicio de documentos si
  ya existe un lector para su tipo, y convierte los avisos de esa lectura en flags. Los
  documentos que la extraccion no pudo bajar tambien quedan como flags (bloqueantes si no
  son reintentables, de advertencia si si lo son).
- `add_client_upload`: lo mismo, pero para un documento que el cliente manda por chat.

## Por que `doc_type` de `CaseDocument` es texto libre y no el enum del conector

El enum `DocumentType` del dominio (`RUT`, `EXOGENA`...) es el catalogo cerrado de lo que
el conector sabe bajar del portal. Los documentos que sube el cliente (certificado de
intereses, registro civil, PILA, predial...) son un catalogo de producto que va a crecer
sin parar, y no tiene nada que ver con el portal. Forzarlos al mismo enum obligaria a
tocar el dominio del conector cada vez que el producto agregue un tipo de certificado.

`CaseDocument.doc_type` es `str`. Para que igual el documento se pueda guardar con la
misma convencion de rutas del `DocumentStore` (que si indexa por `DocumentType`), se
agrego un valor generico `DocumentType.CLIENT_DOCUMENT` como cubo fisico de
almacenamiento; el tipo real y con significado de producto vive en `CaseDocument.doc_type`
y en ningun otro lado.

## Lo que se prueba y por que importa

`test_un_documento_con_lector_queda_leido_automaticamente` es la prueba mas importante de
esta fase: verifica que al vincular una extraccion DIAN, la exogena no solo queda
guardada, queda **leida**, con sus valores accesibles por nombre de campo. Ese es el
punto exacto donde el conector, la lectura de documentos y el expediente dejan de ser
tres piezas sueltas y se convierten en un dato consultable para el motor tributario y
para la consola.

Verificado tambien contra una extraccion real: al vincular RUT, exogena y facturas
electronicas de una cuenta real, los tres quedaron leidos automaticamente y goldeo un
flag real (`TEXT_ENCODING_DAMAGED`, el defecto de codificacion conocido del portal en la
exogena), sin intervencion manual.

## Pendiente

- El motor tributario, que leera del expediente (via `CaseDocument.reading`) en vez de
  archivos sueltos.
- Deteccion automatica de `doc_type` cuando el cliente sube algo sin que se le pidiera.
- Endpoint de descarga del contenido de un documento del expediente (hoy el
  `download_url` que arma la API apunta al endpoint generico de documentos del conector,
  que solo sirve archivos del `DocumentStore`; ya funciona para ambos origenes porque
  todo documento del expediente, venga de donde venga, se guarda en el mismo store).
