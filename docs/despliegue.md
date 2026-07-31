# Despliegue: Supabase (base) + Railway (aplicación)

Este documento existe porque el despliegue tiene cuatro trampas que no se ven en el código y
que cuestan una noche cada una. Están todas acá, con la razón.

## Lo que hay que tener

| | Dónde | Para qué |
|---|---|---|
| Proyecto de Supabase | supabase.com | Postgres. El plan gratis alcanza (500 MB) |
| Proyecto de Railway | railway.com | La aplicación. ~$2/mes de consumo medido |
| Llave de Anthropic | console.anthropic.com | Los diez extractores con modelo |

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
ANTHROPIC_API_KEY=<la llave>
```

## Las cuatro trampas

### 1. El pooler de Supabase contra asyncpg

Supabase da dos URIs y la diferencia importa:

- **puerto 6543** (pooler, modo transacción) — el recomendado, y el que **rompía** asyncpg.
- **puerto 5432** (sesión o conexión directa) — funciona, pero la directa suele ser solo IPv6.

asyncpg usa sentencias preparadas por defecto, y un pooler en modo transacción reparte cada
transacción por una conexión distinta: la sentencia preparada en una no existe en la siguiente.
El síntoma es un error de sentencia duplicada o inexistente **que no menciona el pooler** y
parece un bug de la aplicación.

Ya está resuelto en `adapters/persistence/engine.py`: con `+asyncpg` la caché de sentencias se
apaga y el pool de SQLAlchemy se desactiva (`NullPool`), porque el pooler ya agrupa y dos capas
de pool multiplican conexiones ociosas contra un límite que no es nuestro. **Verificado contra
Postgres real**, no solo contra la suite (que corre en SQLite).

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

### 4. Una sola réplica

El worker de jobs corre **dentro del mismo proceso** del API. Con dos réplicas hay dos workers
compitiendo por la misma cola (hay lease, pero es trabajo desperdiciado y contención inútil), y
si algún día se prende el "app sleep" de Railway, un servicio dormido **deja de sacar trabajos
de la cola**: las consultas a la DIAN se quedan encoladas sin que nadie sepa por qué.

## El navegador de la DIAN

El conector entra por REST y usa Chromium solo como respaldo. Con `DECLARAS_DIAN_ADAPTER=http`
el despliegue es trivial. Para el respaldo hay que pasar a un Dockerfile con
`playwright install --with-deps chromium`: son ~400 MB de imagen y sube el consumo de RAM en
sesión, lo que puede sacar la cuenta del plan de $5.

## El front

Es un **segundo servicio de Railway**, desde el repo `declaras/declaras-front` (rama `dev`).

En desarrollo el proxy de Vite inyecta la llave y el navegador nunca la tiene; ese proxy no
existe al publicar. `server.mjs` cumple ese papel en producción: sirve `dist/` y reenvía `/api`
al backend agregando `X-API-Key`. Sin dependencias — es un intermediario que toca datos
tributarios y cada paquete es superficie que auditar.

Sus dos variables:

```
DECLARAS_API_URL=https://<el-servicio-del-back>.up.railway.app
DECLARAS_API_KEY=<la MISMA de DECLARAS_API_KEYS del back>
```

Verificado en local contra el backend real: la llave no aparece ni en el HTML ni en el bundle,
la subida de documentos pasa por el proxy y se lee (es lo que un proxy mal hecho rompe, porque
el cuerpo va como flujo), las rutas de la aplicación resuelven, y un intento de salir de `dist`
con `..` devuelve el index en vez de un archivo del contenedor.

**Lo que NO es:** autenticación de usuarios. Cualquiera que alcance esa URL usa el backend con
la llave, porque la consola todavía no distingue personas — y el `quien` de cada resolución va
fijo en "contador". Alcanza para operar el demo; el día que entre un contribuyente a ver SU
declaración, la identidad va en la aplicación, no en el proxy.

## Consumo medido

Medido en local sobre el proceso real, no estimado:

| | Medido | Tarifa Railway | Al mes |
|---|---|---|---|
| RAM | 108 MB, estable con carga | $10/GB/mes | ~$1,10 |
| CPU | 0,1–0,2% en reposo | $20/vCPU/mes | ~$0,20–0,60 |

El tier gratis de Railway ($1 de crédito) **no alcanza**: la RAM sola se lo come. El plan Hobby
de $5 sobra. El costo que sí escala con el uso es el de las lecturas con modelo, que factura
Anthropic aparte y es independiente de esto.

Y Supabase gratis **pausa el proyecto tras una semana sin actividad**: si la demostración es
esporádica, hay que despertarlo antes.
