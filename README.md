# declaras

Backend de Declaras: declaracion de renta automatica (Colombia).

El frontend vive en [declaras/declaras-front](https://github.com/declaras/declaras-front).

Este repositorio es el monorepo del backend. Hoy contiene el **conector DIAN**; sobre
la misma base entraran despues el motor tributario y la capa agentica.

---

## Que hace el conector DIAN

Autentica en el portal Muisca con las credenciales del contribuyente, descarga los
cinco insumos de la declaracion, los almacena con su evidencia de auditoria y expone
todo por HTTP para que el agente conversacional lo consuma.

| Documento | Para que sirve |
|---|---|
| `RUT` | Identificacion y actividad economica |
| `EXOGENA` | Lo que terceros le reportaron a la DIAN: base del borrador |
| `PRIOR_RETURN` | Declaracion del anio anterior: patrimonio inicial, anticipo, saldos |
| `SUGGESTED_RETURN` | Declaracion sugerida por la DIAN, cuando existe |
| `EINVOICE_SUMMARY` | Facturas electronicas recibidas: deduccion del 1% |

Ademas hace cumplir tres reglas que no son negociables:

1. **La clave nunca se persiste.** Vive en memoria, cifrada por el proceso, y se
   destruye al terminar el job o al vencer su TTL. Los logs la enmascaran.
2. **Nunca se arriesga la cuenta del usuario.** La DIAN bloquea al tercer intento
   fallido, asi que un contador en base de datos corta en el segundo.
3. **Un documento faltante no tumba la extraccion.** Se reporta como falla parcial con
   su codigo, y el resto se entrega.

---

## Como correrlo

```bash
cp .env.example .env
make install          # uv sync --all-extras --dev
make api              # http://localhost:8000/docs
```

Hay tres adaptadores del conector DIAN, intercambiables con una variable de entorno:

| `DECLARAS_DIAN_ADAPTER` | Que hace | Cuando usarlo |
|---|---|---|
| `http` (por defecto) | Habla HTTP directo con el portal: login por su servicio de identidad y documentos por postback JSF. Sin navegador | Produccion. Login mas RUT en unos 4 segundos |
| `playwright` | Automatiza Chromium | Flujos que dependan de JavaScript y respaldo si el portal endurece validaciones. Requiere `make browsers` |
| `fake` | Conector deterministico en memoria, sin red | Desarrollo y pruebas. Permite integrarse a la API sin credenciales reales |

Otros comandos: `make test`, `make lint`, `make typecheck`, `make check`.

### Probarlo a mano

Con la API corriendo, en otra terminal:

```bash
make probar        # recorre todas las ramas contra localhost:8000
make logs          # sigue el log del servidor
make stop          # baja el servidor
```

`scripts/probar.sh` ejercita en orden: extraccion exitosa, descarga de un documento,
subconjunto de documentos, exito parcial (exogena sin publicar), portal caido con
reintentos, el patron relevo completo (reto de identidad respondido mal y bien), el
freno anti bloqueo de cuenta, la autenticacion, la ausencia de secretos en los logs y
los archivos en disco. Usa cedulas aleatorias en cada corrida para no arrastrar estado.

Tambien hay Swagger interactivo en <http://localhost:8000/docs>.

### Probarlo contra el portal REAL de la DIAN

El portal Muisca no tiene API y su login es una app Angular, asi que los selectores hay
que calibrarlos contra la realidad. El orden importa, porque **la DIAN bloquea la cuenta
al tercer intento fallido**: primero se valida sin credenciales, y solo despues se entra.

```bash
make browsers      # una sola vez: instala chromium

# 1. Sin credenciales: verifica que los selectores del login coincidan con el portal
make calibrar

# 2. Ensayo: llena el formulario real con datos ficticios y confirma que el portal
#    habilita el boton "Ingresar". NO envia nada, asi que no gasta intentos.
make ensayo

# 3. Login real: entra con tu cedula (la clave se pide por teclado) y mapea el portal
#    por dentro. Consume UN intento de login, asi que la clave debe ser correcta.
make explorar CC=1020304050
```

El paso 3 deja en `var/exploracion/` las capturas y un `mapa.json` con los enlaces y
menus reales de cada pagina interna. Ese mapa es el insumo para calibrar los
descargadores de documentos.

Para correr la extraccion completa contra el portal real, en `.env`:

```bash
DECLARAS_DIAN_ADAPTER=playwright
DECLARAS_DIAN_HEADLESS=false      # false para ver el navegador mientras trabaja
```

### Estado de calibracion

Verificado end to end contra el portal real el 2026-07-25: `POST /v1/extractions` con
credenciales reales devolvio **los cinco insumos en 13 segundos**, almacenados y
descargables. Solo el RUT toma unos 4 segundos por HTTP; con navegador, 21.

| Parte | Estado |
|---|---|
| Login por HTTP (servicio de identidad, clave en base64, headers Origin/Referer) | **Calibrado y verificado** |
| Login por navegador (formulario Angular, mat-select, consentimiento) | **Calibrado y verificado** |
| Puntos de entrada del portal (ids JSF estables) | **Mapeados**: ver `endpoints.py` y `DashboardSelectors` |
| Descarga de RUT (postback JSF) | **Calibrado y verificado** en ambos adaptadores |
| Descarga de exogena (modal de anio, 3 envios) | **Calibrado y verificado**: entrega XLSX |
| Resumen de facturas electronicas (mismo modal de anio) | **Calibrado y verificado**: entrega XLSX |
| Declaracion presentada del anio anterior | **Calibrado y verificado**: PDF por la API REST |
| Borrador abierto del anio en curso | **Calibrado y verificado**: PDF por la API REST |
| Navegacion del menu lateral (arbol JSF, resuelto por etiqueta) | **Calibrado** |
| Acceso a la API REST (canje de sesion por token) | **Calibrado y verificado** |
| Verificacion de identidad (reto) | Sin observar: esta cuenta no lo pidio |

Aprendizaje clave que condiciona el resto: **el Muisca es JSF y no se navega por URL**.
Los enlaces son `href="#"` y la navegacion ocurre por postback del formulario. Lo estable
son los ids de los botones (`btnConsultarRUT`, `btnExogena`), que son semanticos. El
conector HTTP reenvia el formulario con sus campos ocultos mas las coordenadas del boton;
el de navegador hace clic. Detalle completo en [ADR 0003](docs/adr/0003-conector-http-en-vez-de-navegador.md).

---

## Contrato de la API

Autenticacion: header `X-API-Key`. Todo error trae `code`, `message`, `retryable`,
`details` y el header `X-Retryable`.

Las operaciones son **asincronas a proposito**: una extraccion tarda minutos y el
portal se cae, asi que nunca se deja una conexion HTTP esperando.

### 1. Encolar una extraccion

```http
POST /v1/extractions
X-API-Key: <key>

{
  "id_kind": "CC",
  "id_number": "1020304050",
  "dian_password": "clave-del-portal",
  "doc_types": ["RUT", "EXOGENA", "EINVOICE_SUMMARY"],
  "callback_url": "https://agente.declaras.co/hooks/extraccion"
}
```

**El anio gravable no se pide.** La renta se declara el anio siguiente al que cierra, asi
que durante 2026 se consulta el anio gravable 2025: es una regla del calendario y el
sistema la deduce. Solo se envia `tax_year` explicito para poner al dia declaraciones de
anios pasados.

Responde `202` con `job_id` y header `Location`.

### 2. Consultar el estado

```http
GET /v1/extractions/{job_id}
```

`status` es uno de: `QUEUED`, `RUNNING`, `AWAITING_CHALLENGE`, `SUCCEEDED`, `FAILED`,
`CANCELLED`. Si hay `callback_url`, se notifica al terminar y no hace falta hacer polling.

### 3. Responder la verificacion de identidad (patron relevo)

Cuando el portal pide un codigo del correo o preguntas de seguridad, el job queda en
`AWAITING_CHALLENGE` con el detalle en `challenge`, y la sesion del navegador se
mantiene viva. El agente le pregunta al usuario por WhatsApp y responde:

```http
POST /v1/extractions/{job_id}/challenge
{ "answers": ["1234"] }
```

El job vuelve a la cola y continua con la misma sesion.

### 4. Descargar documentos y cancelar

```http
GET  /v1/documents/content?uri=<storage_uri>
POST /v1/extractions/{job_id}/cancel
```

### Codigos de error

| `code` | HTTP | Reintentable | Que debe hacer el agente |
|---|---|---|---|
| `DIAN_INVALID_CREDENTIALS` | 401 | no | Pedir la clave de nuevo. `details.attempts_remaining` dice cuantos intentos quedan antes de que bloqueemos |
| `DIAN_LOGIN_ATTEMPTS_EXHAUSTED` | 429 | no | No insistir. Ofrecer recuperacion de clave |
| `DIAN_ACCOUNT_LOCKED` | 423 | no | La cuenta ya esta bloqueada en la DIAN: guiar recuperacion |
| `DIAN_IDENTITY_CHALLENGE` | 409 | no | Responder el reto antes de seguir |
| `DIAN_PORTAL_UNAVAILABLE` | 503 | si | Avisar que el portal esta caido; el worker reintenta |
| `DIAN_PORTAL_TIMEOUT` | 504 | si | Igual que el anterior |
| `DIAN_RATE_LIMITED` | 429 | si | Esperar |
| `DIAN_SESSION_EXPIRED` | 440 | si | Reiniciar la extraccion (pedir clave otra vez) |
| `DIAN_DOCUMENT_UNAVAILABLE` | 404 | no | El documento no existe aun (exogena sin publicar): seguir sin el |
| `DIAN_LAYOUT_CHANGED` | 502 | no | **Alerta al equipo**: el portal cambio y hay que recalibrar selectores |
| `JOB_NOT_FOUND` / `JOB_STATE_CONFLICT` | 404 / 409 | no | Error de uso de la API |
| `VALIDATION_ERROR` | 422 | no | Datos invalidos |

### Escenarios del conector falso

La rama se escoge con el contenido de `dian_password`, lo que permite probar todas las
ramas sin el portal real:

| Clave contiene | Resultado |
|---|---|
| `bad` | `DIAN_INVALID_CREDENTIALS` |
| `locked` | `DIAN_ACCOUNT_LOCKED` |
| `down` | `DIAN_PORTAL_UNAVAILABLE` |
| `slow` | `DIAN_PORTAL_TIMEOUT` |
| `challenge` | Reto de identidad; se resuelve respondiendo `1234` |
| `noexo` | Exito parcial: la exogena falla, los demas documentos bajan |
| cualquier otra | Exito completo |

---

## Arquitectura

```
src/declaras/
  domain/        errores, modelos y puertos. Cero I/O, cero framework
  services/      casos de uso: extraccion, worker, boveda, registro de sesiones
  adapters/      dian (Playwright), storage (local/GCS), persistence (SQLAlchemy)
  api/           FastAPI: contratos, autenticacion, contenedor de dependencias
```

La dependencia siempre apunta hacia adentro: `api -> services -> domain`, y los
adaptadores implementan los puertos que define el dominio. Por eso el conector real se
puede cambiar por el falso con una variable de entorno.

Decisiones de diseno documentadas en [docs/adr](docs/adr).

### Almacenamiento

Los documentos se guardan en `{subject_hash}/{anio}/{tipo}/{sha}.{ext}`. El numero de
documento **no** viaja en la ruta: se usa un hash estable, y la trazabilidad se
mantiene por el job. Backend local para desarrollo, GCS para produccion (`--extra gcs`).

La retencion minima es de 3 anios, que es cuando la declaracion queda en firme.

---

### La API REST de la DIAN

Las declaraciones no estan en el portal JSF sino detras de `api.dian.gov.co`, la API que
consumen las aplicaciones Angular de la entidad. El acceso esta implementado en
`DianApiClient`: se canjea la cookie de sesion del portal por un Bearer token y se consulta
con el header `clientid`, que la API exige.

Ademas de los documentos, la API permite saber antes de pedirle un dato al cliente **que
declaraciones tiene presentadas, de que anios, si hay un borrador abierto, si admite
correccion y si tiene firma electronica**. Catalogo completo en
[ADR 0004](docs/adr/0004-api-rest-de-la-dian.md).

Siguiente frontera: la misma API expone el formulario 210 en `documentos/renta210v18/v1`,
que es el camino del presentador. Diligenciar por API en vez de manejar un navegador sobre
una SPA convierte la pieza mas fragil del producto en una integracion normal.

## Notas para quien parsee los documentos

Las rutas de almacenamiento agrupan por el anio gravable que se esta preparando, no por
el anio propio de cada documento: la declaracion de 2024 vive bajo `2025/prior_return/`
porque es un insumo de la declaracion de 2025. El anio real del documento queda en sus
metadatos.

El XLSX de exogena trae una hoja con el detalle por tercero: NIT y razon social de quien
reporta, concepto, valor, si la DIAN lo uso en la declaracion sugerida, y los topes de
obligacion. Es el insumo directo del borrador.

Cuidado con la codificacion: el portal sirve el archivo declarando UTF-8 pero con bytes en
ISO-8859-1, asi que los nombres con tildes llegan con caracteres de reemplazo. Es un
defecto del portal, no del conector: los bytes se almacenan tal cual llegan y el parser
debe decodificar como `latin-1` cuando encuentre secuencias invalidas.

## Lectura de documentos

Ademas del conector, hay un servicio que convierte cada documento (del portal o subido
por el cliente) en valores estructurados con su confianza y procedencia. Dos familias de
lectores: deterministicos (XLSX y PDF del portal, sin IA) y por vision (fotos del
cliente, pendiente de implementar). Decision y hallazgos completos en
[ADR 0005](docs/adr/0005-lectura-de-documentos-deterministica-vs-vision.md).

```
GET  /v1/documents/types                     tipos con lector disponible
POST /v1/documents/read         (multipart)  lee un archivo subido directamente
POST /v1/documents/read-stored  (json)       lee un documento que el conector ya bajo
```

Documentos con lector: `EXOGENA` (XLSX, incluye el renglon del 210 que la propia DIAN
asigna a cada valor reportado), `EINVOICE_SUMMARY` (XLSX, agrega la base de la deduccion
del 1% ya filtrada por medio de pago) y `RUT` (PDF sin campos de formulario, lectura
posicional con confianza declarada como baja).

## Pendiente antes de produccion

- [ ] **Calibrar los descargadores de documentos** contra una sesion autenticada. El
      login ya esta calibrado; las paginas internas requieren `make explorar`.
- [ ] Cifrado en reposo con KMS si alguna vez hay que persistir credenciales.
- [ ] Migraciones con Alembic cuando el esquema empiece a evolucionar con datos reales.
- [ ] Alertas sobre `DIAN_LAYOUT_CHANGED`: es la senal de que el portal cambio.
- [ ] Trazas distribuidas (OpenTelemetry) cuando entren mas servicios al monorepo.
