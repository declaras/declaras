# ADR 0007: protecciones del expediente

Fecha: 2026-07-25. Estado: aceptado. Origen: revision critica del expediente.

## Contexto

Una revision del expediente recien construido encontro tres fallas reales, confirmadas con
pruebas antes de corregirlas. Las tres compartian la misma causa de fondo: el servicio
confiaba en que quien lo llama le pasa datos coherentes.

## Las fallas y su correccion

### 1. Se podia vincular la extraccion de un contribuyente al expediente de otro

`link_extraction_result` recibia un `case_id` y un job de extraccion y no comparaba a quien
pertenecia cada uno. Una extraccion de la cedula 99999999 entraba sin resistencia al
expediente de la cedula 11111111. Mezclar informacion tributaria de dos personas es el peor
dano que este sistema puede hacer: contamina el calculo del impuesto y es casi imposible de
detectar despues, porque el expediente se ve perfectamente normal.

Correccion: `_assert_same_taxpayer` compara tipo y numero de documento, y tambien el anio
gravable (para que documentos de 2024 no entren a un expediente de 2025). Si no coinciden,
lanza `TaxpayerMismatchError` (409) y el expediente no queda tocado.

### 2. Un documento ilegible no generaba ninguna alerta

El servicio de lectura lanzaba `ValidationError` tanto cuando no hay parser para un tipo de
documento como cuando el archivo esta corrupto. El expediente capturaba esa excepcion
generica y seguia de largo en ambos casos, asi que un XLSX corrupto etiquetado como exogena
se guardaba sin lectura y **sin flag**: el contador nunca se enteraba de que habia que
volver a pedirlo.

Correccion: dos errores distintos. `UnsupportedDocumentTypeError` (no hay parser todavia:
limitacion conocida del sistema, no ensucia el expediente) y `DocumentUnreadableError` (el
archivo deberia poder leerse y no se pudo: genera flag bloqueante). La distincion tambien le
sirve al agente conversacional, porque uno se resuelve pidiendo el documento de nuevo y el
otro no tiene sentido reintentarlo.

### 3. Vincular la misma extraccion dos veces duplicaba todo

Un reintento del agente (por timeout o reenvio) duplicaba documentos, flags y eventos de la
bitacora.

Correccion: si el job ya se vinculo, se devuelve el expediente tal como esta. Ademas se
omiten los documentos cuyo par (tipo, hash del contenido) ya esta en el expediente, para el
caso de dos extracciones distintas que traen el mismo archivo byte a byte.

## Dos protecciones adicionales que salieron de la misma revision

**Documento a nombre de otra persona.** Casi todos los documentos del portal traen el numero
de identificacion del contribuyente. `_flag_if_identity_differs` compara ese numero con el
del cliente del expediente y, si no coinciden, levanta un flag bloqueante. Cubre un caso
real del producto: el cliente sube por error el certificado de su pareja.

**Integridad interna del RUT.** El parser del RUT es posicional sobre un PDF sin campos de
formulario, y su peor modo de falla no es no encontrar un valor, sino devolver el valor
equivocado con aparente normalidad. En una persona natural el NIT y el numero de
identificacion son el mismo numero, asi que compararlos es una prueba gratuita de que el
cursor no se desincronizo. Si difieren, se emite el aviso `RUT_ID_MISMATCH`.

## Otros dos arreglos menores

- `resolve_flag` no verificaba que el flag perteneciera al expediente de la ruta: se podia
  resolver el flag de un expediente pasando el id de otro, y la bitacora quedaba contando
  una historia falsa. Ahora valida la pertenencia y registra el evento.
- El parametro `job_id` de `DocumentStore.put` se renombro a `scope_id`. El expediente le
  pasaba un `case_id`, asi que el nombre mentia; `scope_id` describe lo que realmente es
  (el id que agrupa la evidencia de la operacion, sea un job o un expediente).

## Cuarta falla, encontrada al usar la consola con datos reales

**Reconsultar la DIAN duplicaba los documentos.** La proteccion de idempotencia cubria el
caso de vincular el MISMO job dos veces, y ademas omitia documentos cuyo par (tipo, hash
del contenido) ya estuviera en el expediente. Eso resulto insuficiente por una razon que
solo se ve con archivos reales: **la DIAN incrusta la fecha de generacion dentro del
archivo** (el RUT trae "Fecha generacion documento PDF" y la exogena trae "Fecha Reporte"),
asi que cada descarga del mismo documento tiene un hash distinto y la comparacion por
contenido nunca acierta.

Y reconsultar no es un caso raro: es lo normal. El contador vuelve a consultar cuando la
DIAN ya publico la exogena, o cuando el cliente actualizo su RUT. Con la dedup por hash, el
expediente terminaba con dos RUT, dos exogenas y los avisos duplicados, sin forma de saber
cual documento era el vigente.

Correccion: los documentos del portal se **reemplazan**, no se acumulan. Al vincular una
consulta nueva, los documentos vigentes de ese mismo tipo se marcan `superseded_at` y el
nuevo queda como el unico vigente. La copia anterior no se borra (la DIAN puede preguntar
hasta tres anios despues, y la bitacora debe poder reconstruir que se vio en cada momento):
queda en `CaseDetail.superseded_documents`. Los avisos que apuntaban al documento
reemplazado se resuelven solos con una nota, porque un aviso sobre un documento que ya no
es el vigente solo ensucia la lista de pendientes.

## Leccion que queda

Un servicio que recibe identificadores de dos agregados distintos (un expediente y un job)
tiene que verificar que hablen de lo mismo. No alcanza con que cada pieza este bien probada
por separado: la falla vive en la costura.
