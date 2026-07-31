# Despliegue: Supabase (base) + Railway (aplicación)

Este documento existe porque el despliegue tiene cinco trampas que no se ven en el código y
que cuestan una noche cada una. Están todas acá, con la razón.

## Lo que hay que tener

| | Dónde | Para qué |
|---|---|---|
| Proyecto de Supabase | supabase.com | Postgres. El plan gratis alcanza (500 MB) |
| Proyecto de Railway | railway.com | La aplicación. ~$2/mes de consumo medido |
| Llave de Gemini | aistudio.google.com | Los diez extractores con modelo |

**Ninguna de las tres se escribe en el repositorio.** Van a las variables de Railway.

## Variables

```
DECLARAS_ENV=production
DECLARAS_LOG_LEVEL=INFO
DECLARAS_API_KEYS=<una llave larga y nueva; NO la de desarrollo>
DECLARAS_DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<pass>@<region>.pooler.supabase.com:6543/postgres
DECLARAS_STORAGE_BACKEND=local          # ver "los documentos" abajo
DECLARAS_STORAGE_LOCAL_ROOT=/data/documents
DECLARAS_DIAN_ADAPTER=http              # sin Chromium; ver "el navegador" abajo
DECLARAS_WORKER_ENABLED=true
GEMINI_API_KEY=<la llave>
```

## Las cinco trampas

### 1. El pooler de Supabase contra asyncpg

Supabase da dos URIs y la diferencia importa:

- **puerto 6543** (pooler, modo transacción) — el recomendado, y el que **rompía** asyncpg.
- **puerto 5432** (sesión o conexión directa) — funciona, pero la directa suele ser solo IPv6.

asyncpg usa sentencias preparadas por defecto, y un pooler en modo transacción reparte cada
transacción por una conexión distinta: la sentencia preparada en una no existe en la siguiente.
El síntoma es un error de sentencia duplicada o inexistente **que no menciona el pooler** y
parece un bug de la aplicación.

Ya está resuelto en `adapters/persistence/engine.py`: con `+asyncpg` la caché de sentencias se
apaga. **Verificado contra la Supabase real**, no solo contra la suite (que corre en SQLite):
conecta, crea las 11 tablas y el flujo completo pasa.

Y **sí hay pool del lado del cliente**, aunque el pooler también agrupe. Estuvo con `NullPool`
—que es lo correcto en serverless, donde el proceso muere entre requests— y medido contra
Supabase eso costaba 3 segundos por request en handshakes: sin pool, cada operación abre una
conexión nueva, y este servicio hace ~24 idas a la base por request. Esto corre en un contenedor
de vida larga y ahí reutilizarlas es el punto. El pool se deja chico porque el límite de
conexiones del proyecto no es nuestro.

### 2. El esquema se crea, pero NO se migra

El arranque corre `metadata.create_all`, que **crea las tablas que faltan y nunca altera una
columna que cambió**. En desarrollo con SQLite eso es invisible porque uno borra el archivo;
contra Supabase, el día que cambie un modelo el esquema queda desalineado **en silencio** y los
errores parecen bugs de la aplicación.

**Sin Alembic, cualquier cambio de modelo obliga a recrear la base a mano.** Es aceptable
mientras los datos sean de prueba y deja de serlo el día que haya un caso real: la DIAN puede
preguntar por una declaración hasta tres años después.

### 3. Los documentos se borran en cada deploy

El disco de Railway es efímero: con `DECLARAS_STORAGE_BACKEND=local`, cada despliegue se lleva
los PDF que subió el cliente. Dos salidas:

- **Volumen de Railway** montado en `/data` — rápido de montar, ata los archivos a esa máquina.
- **Supabase Storage** — es compatible con S3, y el puerto de almacenamiento son dos métodos
  (`put` y `read`) con dos implementaciones ya escritas, así que es un archivo más.

Mientras se decida, el volumen sirve; lo que no sirve es dejarlo en el disco por defecto.

### 4. La región NO es una preferencia: multiplica por cien

Medido contra la Supabase real: **~24 consultas por request** (un `GET /conciliacion` lee el
estado, los renglones, la huella y la liquidación). La latencia de red se multiplica por ese
número.

| Desde | Ida y vuelta | Un request | |
|---|---|---|---|
| Railway `us-east4` + Supabase `us-east-1` | 1–3 ms | **~0,05 s** | mismo metro: Ashburn |
| Railway `us-east4` + Supabase `us-east-2` | ~12 ms | ~0,3 s | Ohio, 500 km |
| Costas cruzadas (Railway este, base oeste) | ~70 ms | ~1,7 s | |
| Base en São Paulo, Railway en EE. UU. | ~120 ms | ~2,9 s | la peor: la co-ubicación pesa 24× |
| Medido desde Colombia a `us-east-1` | 240 ms | **5,8 s** | |
| Medido desde Colombia a `us-west-2` | 352 ms | 8,4 s | |

Las dos últimas filas son reales y las medí; no son un defecto del código, son la distancia desde
un portátil. Lo que importa es la primera: **el servicio de Railway y el proyecto de Supabase van
en la misma región**, y el par elegido es `us-east4` con `us-east-1` porque el identificador de
Railway (`us-east4-eqdc4a`, Equinix DC4) está en Ashburn, el mismo área metropolitana que AWS
`us-east-1`.

Y la fila de São Paulo explica por qué no se pone la base cerca del usuario: la latencia a la
base se multiplica por 24 y la del navegador se paga UNA vez. Acercar la base al cliente y
alejarla de la aplicación cambia 50 ms por tres segundos.

(Y de paso: 24 consultas para un `GET` es mucho. No es urgente con 5 ms de ida y vuelta, pero
es deuda: el día que haya que apretar, ahí está el margen.)

### 5. Una sola réplica

El worker de jobs corre **dentro del mismo proceso** del API. Con dos réplicas hay dos workers
compitiendo por la misma cola (hay lease, pero es trabajo desperdiciado y contención inútil), y
si algún día se prende el "app sleep" de Railway, un servicio dormido **deja de sacar trabajos
de la cola**: las consultas a la DIAN se quedan encoladas sin que nadie sepa por qué.

## El proveedor de extracción, verificado contra el API real

Los diez extractores hablan con Gemini (`gemini-3.6-flash`) desde un solo sitio,
`extraccion/_base.py`. Cambiar de proveedor es ese archivo y el doble de las pruebas; los diez
esquemas, los diez prompts y los guards de cada certificado no se tocan.

Verificado con una llave de verdad sobre un 220 sintético, no solo con el doble:

| Caso | Resultado |
|---|---|
| Certificado correcto | Lee los diez campos exactos, incluido el NIT sin dígito de verificación |
| Total impreso que no cuadra | Rechaza: "los campos suman 87.400.000 y el certificado dice 95.000.000" |
| Certificado de otro año | Rechaza nombrando los dos años |
| Certificado que trae pensiones | Rechaza y dice que van como `IngresoPension` |

Los tres rechazos importan más que la lectura correcta: son la razón por la que cambiar de
proveedor es de bajo riesgo. Cada extractor reconcilia lo que el modelo leyó contra un total
impreso en el propio documento, así que un modelo que lea peor no produce una cifra equivocada
— produce un rechazo, que es visible. Para repetirlo: `scripts/probar_extractor.py`.

## El navegador de la DIAN

El conector entra por REST y usa Chromium solo como respaldo. Con `DECLARAS_DIAN_ADAPTER=http`
el despliegue es trivial. Para el respaldo hay que pasar a un Dockerfile con
`playwright install --with-deps chromium`: son ~400 MB de imagen y sube el consumo de RAM en
sesión, lo que puede sacar la cuenta del plan de $5.

## El front

Vive en **Vercel**, no en Railway: son cinco páginas prerenderizadas para buscadores y un CDN,
y un contenedor no aporta nada ahí. Proyecto `declaras` en la cuenta `sergiosteam`,
`https://declaras.vercel.app`.

El proxy que inyecta la llave es una función, `api/proxy.js`, con un rewrite explícito en
`vercel.json` (`/api/(.*)` → `/api/proxy?ruta=$1`). Sus dos variables:

```
DECLARAS_API_URL=https://back-production-a062.up.railway.app
DECLARAS_API_KEY=<la MISMA de DECLARAS_API_KEYS del back>
```

Verificado en producción: la llave no aparece en el HTML ni en el bundle, el circuito completo
responde por el proxy (conciliación, peticiones, liquidación, borrador, y la **subida de un
documento**), y las cinco páginas sirven ~4.000 palabras sin ejecutar JavaScript.

**El límite que hay que conocer:** una función de Vercel acepta 4,5 MB de cuerpo y devuelve 413
al pasarlo. Un 220 exportado por una nómina pesa ~100 KB; uno **escaneado** puede pesar 5-10 MB,
y son justo los que más necesitan al extractor. No se tapa con un reintento. La salida de verdad
es que el navegador le hable al backend directo, lo que exige autenticar al **usuario** y no a un
servicio: el día que exista el login del contribuyente, esa función se borra.

**El auto-deploy no está conectado.** La cuenta de Vercel no tiene acceso a la org `declaras` en
GitHub, así que hay que autorizar su app de GitHub para esa org. Mientras, se despliega con
`vercel --prod --scope sergiosteam`.

## Consumo medido

Medido en local sobre el proceso real, no estimado:

| | Medido | Tarifa Railway | Al mes |
|---|---|---|---|
| RAM | 108 MB, estable con carga | $10/GB/mes | ~$1,10 |
| CPU | 0,1–0,2% en reposo | $20/vCPU/mes | ~$0,20–0,60 |

El tier gratis de Railway ($1 de crédito) **no alcanza**: la RAM sola se lo come. El plan Hobby
de $5 sobra. El costo que sí escala con el uso es el de las lecturas con modelo, que factura Google
aparte y es independiente de esto.

Y Supabase gratis **pausa el proyecto tras una semana sin actividad**: si la demostración es
esporádica, hay que despertarlo antes.
