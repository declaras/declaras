# declaras

Backend de Declaras: declaracion de renta automatica (Colombia).

El frontend vive en [declaras/declaras-front](https://github.com/declaras/declaras-front).

Este repositorio es el monorepo del backend. Hoy contiene el **conector DIAN** y el
**motor tributario**; sobre la misma base entrara despues la capa agentica.

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
| `sindecl` | Primerizo: no hay declaracion anterior ni borrador. **Es el caso real del primer contribuyente**, verificado contra el portal el 2026-08-08 |
| cualquier otra | Exito completo |

El recorrido completo con esas ramas vive en `tests/integration/test_recorrido_mvp.py`: cada
prueba es una parada, de abrir el expediente a comparar contra la DIAN. Su regla es que ninguna
parada se quede muda — o entrega el dato, o dice por que no puede.

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

### El tunel a Colombia: por que hay un VPS

`api.dian.gov.co` **corta el handshake TLS si la peticion no sale de Colombia**. No
responde 403 ni cierra el TCP: acepta la conexion y despues abandona el handshake, que
llega al codigo como un `ConnectError` generico. `muisca.dian.gov.co` —el portal JSF, de
donde salen RUT, exogena y facturas— no tiene ese bloqueo y responde desde cualquier parte.

Por eso el contenedor de Railway (Virginia) levanta un **SOCKS5 por SSH contra una maquina
en Bogota** y enruta por ahi *solo* ese host. Todo lo demas sigue saliendo directo.

**El bloqueo es por geolocalizacion, no por registro.** Vale la pena saberlo porque cuesta
una tarde: el rango de esta IP figura en ARIN a nombre de **Cogent Communications (US)**,
y aun asi la DIAN la acepta, porque las bases de geolocalizacion la ubican en Bogota. Un
`whois` que diga "US" **no** es motivo para descartar un proveedor sin probarlo.

#### La maquina

| Dato | Valor |
|---|---|
| Proveedor | LightNode (`AS154177 LIGHT NODE LIMITED`), region Bogota |
| IP | `149.104.107.243` |
| Sistema | Ubuntu 24.04.2 LTS |
| Recursos | 1 vCPU, 2 GB RAM, 50 GB disco |
| Hostname | `cd8vaumr.vm` |
| Puerto SSH | **2222** (ver abajo) |
| Facturacion | por hora |

Un tunel no necesita mas: reenvia bytes cifrados y no procesa nada. La carga medida es
practicamente cero.

#### Por que el SSH esta en el 2222 y no en el 22

**El proveedor bloquea el puerto 22 entrante desde fuera de Colombia.** No es una decision
de seguridad nuestra ni una configuracion del servidor: el 22 escucha y el firewall lo
permite, pero el trafico no llega. Desde Railway el tunel daba `Connection timed out` en
bucle, y el sintoma que aparecia tres capas mas arriba era "no se pudo conectar con la API
de la DIAN". El 2222 y el 443 pasan sin problema.

Desde dentro de Colombia el 22 sirve; desde Railway, no. **Usa siempre el 2222.**

#### Los dos usuarios, y por que son dos

| Usuario | Para que | Que puede hacer |
|---|---|---|
| `tunel` | Lo usa el backend | **Solo** abrir un forward hacia `api.dian.gov.co:443` |
| `root` | Administrar la maquina | Todo |

La cuenta `tunel` esta encerrada por dos vias independientes. Su shell es
`/usr/sbin/nologin`, y su llave lleva restricciones en la propia linea de
`authorized_keys`:

```
restrict,port-forwarding,permitopen="api.dian.gov.co:443",command="/bin/false" ssh-rsa AAAA...
```

Traducido: `restrict` apaga todo (agente, X11, tty, ejecucion de comandos),
`port-forwarding` vuelve a encender lo unico que se necesita, `permitopen` limita el
destino a ese host y ese puerto, y `command="/bin/false"` remata. Con esa llave nadie
puede leer un archivo del servidor ni usarlo como proxy hacia otro sitio.

Eso importa porque la llave privada **vive en una variable de entorno de Railway**
(`DECLARAS_DIAN_TUNEL_LLAVE`), que es el lugar donde mas facil se filtra un secreto. Las
restricciones son lo que hace que filtrarla sea un incidente acotado y no la perdida del
servidor.

> ⚠️ **Hoy esa garantia no se cumple: la misma llave esta tambien en
> `/root/.ssh/authorized_keys`, sin restricciones.** Quien tenga la llave del tunel entra
> como `root` con solo cambiar el usuario, y todo lo de arriba deja de aplicar. Es un
> residuo del montaje inicial y hay que cerrarlo (ver "Dar acceso a otra persona").

#### Lo que el VPS ve del trafico

Nada legible. El tunel es SOCKS5 sobre SSH: transporta el TLS **sin terminarlo**, asi que
en la maquina solo pasan bytes cifrados entre el backend y la DIAN. Esto no es un detalle
de implementacion, es un requisito: **el intermediario nunca debe poder leer la clave DIAN
de un contribuyente.** Cualquier reemplazo futuro (proxy gestionado, salida NAT, otro
proveedor) tiene que cumplir lo mismo — un proxy HTTP que termine TLS queda descartado.

Ademas, la clave del contribuyente ni siquiera pasa por aqui: se escribe en
`muisca.dian.gov.co`, que sale directo. Por el tunel solo viaja el Bearer token ya
canjeado.

#### Conectarse

```bash
# Administrar la maquina (requiere la llave de administracion)
ssh -p 2222 -i ~/.ssh/declaras_dian_tunel_rsa root@149.104.107.243

# Levantar el tunel a mano, igual que lo hace el contenedor.
# -N: no ejecutar comandos.  -D: abrir un SOCKS5 local en el 1080.
ssh -N -D 127.0.0.1:1080 -p 2222 -i ~/.ssh/declaras_dian_tunel_rsa \
    tunel@149.104.107.243

# Con el tunel arriba, comprobar que la DIAN responde desde Colombia.
# Sin credenciales devuelve 401, que es la respuesta correcta: significa que
# el handshake TLS se completo, que es justo lo que no pasa sin tunel.
curl -s --socks5-hostname 127.0.0.1:1080 \
  "https://api.dian.gov.co/documentos/renta210ingreso/v1/formularios?estado=presentado"
# {"codigo":401,"mensaje":"Unauthorized","descripcion":"The request requires user authentication"}
```

`127.0.0.1` y no `0.0.0.0` **no es cosmetica**: escuchando en todas las interfaces, ese
SOCKS seria un proxy abierto para cualquiera que alcance la maquina.

Sin tunel el mismo `curl` no devuelve un codigo HTTP: se queda colgado y muere en el
handshake. Esa es la diferencia que se esta comprobando.

#### Como lo usa el backend

`scripts/arrancar.sh` levanta el tunel **antes** de uvicorn, en un bucle que lo reabre si
se cae, y despues `exec`uta el servidor para que sea el proceso 1 y reciba las senales de
apagado de Railway. Se configura con cuatro variables:

| Variable | Valor en Railway | Que hace |
|---|---|---|
| `DECLARAS_DIAN_TUNEL_DESTINO` | `tunel@149.104.107.243` | Usuario y host |
| `DECLARAS_DIAN_TUNEL_LLAVE` | la llave privada completa | Llega por variable porque en Railway no hay donde montar un archivo |
| `DECLARAS_DIAN_TUNEL_SSH_PUERTO` | `2222` | Por el bloqueo del 22 |
| `DECLARAS_DIAN_API_PROXY` | `socks5://127.0.0.1:1080` | Lo que hace que el cliente HTTP lo use |

Las dos primeras mandan: **si faltan, el script arranca uvicorn y ya**, sin tunel y sin
error. Es el comportamiento correcto en local y en cualquier despliegue que ya alcance la
DIAN.

La ultima es la que conecta las dos mitades. `DECLARAS_DIAN_API_PROXY` se monta en
`rest/connector.py` con `mounts`, que enruta **por host**: solo `api.dian.gov.co` entra al
tunel. Sin esa variable el tunel queda arriba y sin usar — y el sintoma es que todo se ve
bien salvo que los documentos siguen sin bajar.

#### Comprobar que esta arriba

En los logs de arranque (`railway logs`), el script deja constancia explicita:

```
tunel.dian: levantando SOCKS5 en 127.0.0.1:1080 hacia tunel@149.104.107.243:2222
tunel.dian: OK, alguien escucha en 127.0.0.1:1080
```

Esa segunda linea existe porque **el bucle solo habla cuando `ssh` sale**: un `ssh` colgado
que nunca llega a escuchar se veia identico a uno sano, y el problema aparecia disfrazado
tres capas mas arriba como "no se pudo consultar la DIAN". Si en su lugar dice
`NADIE ESCUCHA`, el tunel no levanto y las consultas a la API van a fallar.

Cuando falla, el mensaje que llega a la pantalla **nombra al tunel**:

> No se pudo conectar con la API de la DIAN a traves del tunel configurado. Puede estar
> caida la DIAN o el tunel.

Es deliberado. Con un texto generico ("la DIAN no responde") quien opere revisa el portal
de la DIAN, lo ve funcionando, y pierde un rato largo antes de sospechar de una maquina
propia que nadie le menciono. Ya paso dos veces.

#### Dar acceso a otra persona

**No repartas la llave del tunel.** Hoy es tambien la llave de `root` (ver el aviso de
arriba), y ademas una llave compartida no se puede revocar por persona. Cada quien lleva
la suya:

```bash
# 1. La persona genera SU par y manda SOLO el .pub
ssh-keygen -t ed25519 -C "nombre-persona-declaras" -f ~/.ssh/declaras_vps

# 2. Desde una sesion de root, se agrega su llave publica
ssh -p 2222 -i ~/.ssh/declaras_dian_tunel_rsa root@149.104.107.243 \
  "echo 'ssh-ed25519 AAAA... nombre-persona-declaras' >> /root/.ssh/authorized_keys"

# 3. Revocar despues es borrar esa linea, sin tocar a nadie mas
```

Y el pendiente que conviene hacer en la misma pasada, **en este orden**, porque hacerlo al
reves deja la maquina inaccesible:

1. Crear una llave de administracion nueva y agregarla a `/root/.ssh/authorized_keys`.
2. **Comprobar que entra.** En otra terminal, sin cerrar la sesion actual.
3. Solo entonces, borrar de `/root/.ssh/authorized_keys` la linea de la llave del tunel.

Al terminar, la llave que vive en Railway solo sirve para lo que dice su nombre.

#### Si algo se rompe

| Sintoma | Causa probable |
|---|---|
| `Connection timed out` al conectar | Se esta usando el puerto 22. Usa el 2222 |
| `NADIE ESCUCHA en 127.0.0.1:1080` | El tunel no levanto: revisa la llave y el destino |
| Tunel arriba pero los documentos no bajan | Falta `DECLARAS_DIAN_API_PROXY` |
| `Permission denied (publickey)` | Llave equivocada, o permisos: `chmod 600` |
| Los cambios de `sshd` no aplican | En `sshd_config` **gana el primer valor**, no el ultimo. El drop-in propio se llama `00-declaras.conf` justo para ganarle a `50-cloud-init.conf`, que trae `PasswordAuthentication yes` |

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

## El expediente

Cliente, expediente por anio gravable, documentos (del portal o subidos por el cliente),
flags que un contador debe revisar y bitacora de auditoria: es el agregado que amarra el
conector DIAN y la lectura de documentos, y la base de la futura consola del contador.
Diseno completo en [ADR 0006](docs/adr/0006-expediente-agregado-central.md).

```
POST /v1/cases                              abre un expediente (crea el cliente si es nuevo)
GET  /v1/cases                              lista expedientes (para la consola)
GET  /v1/cases/{id}                         detalle completo: cliente, documentos, flags, bitacora
POST /v1/cases/{id}/link-extraction         vuelca una extraccion DIAN ya terminada al expediente
POST /v1/cases/{id}/documents  (multipart)  el cliente sube un documento por chat
POST /v1/cases/{id}/flags/{id}/resolve      marca un flag como resuelto
GET  /v1/clients                            lista clientes
GET  /v1/clients/{id}/cases                 expedientes de un cliente, por todos los anios
```

Verificado contra una extraccion real: al vincular RUT, exogena y facturas electronicas,
los tres quedaron leidos automaticamente y salio un flag real (el defecto de codificacion
conocido del portal), sin intervencion manual.

El expediente hace cumplir cuatro protecciones, cada una nacida de un bug real
([ADR 0007](docs/adr/0007-protecciones-del-expediente.md)): no se vincula una extraccion de
otro contribuyente ni de otro anio, vincular dos veces el mismo job es inofensivo, un
documento ilegible genera flag bloqueante (distinto de un tipo que aun no tiene lector), y
un documento a nombre de otra persona frena el expediente.

## Resumen del expediente y semilla del motor tributario

`GET /v1/cases/{id}/summary` devuelve lo que el sistema ya sabe, derivado de lo que leyo:
los cinco topes de obligacion con el valor que la DIAN reporta contra el limite legal del
anio, el agregado por renglon del formulario 210 (usando la asignacion que la propia DIAN
hace de cada valor reportado) y la base de la deduccion del 1% de facturas electronicas.
No calcula el impuesto: organiza y compara con la ley lo que los documentos dicen.

El modulo `declaras/tax` es la base de ese calculo: tabla de UVT por anio (nunca una sola
constante, porque siempre conviven dos: la del anio que se declara y la del anio en curso)
y los topes del articulo 592. La conversion de UVT a pesos **no redondea**, porque los
topes oficiales son la multiplicacion exacta: 1.400 UVT del anio gravable 2025 son
$69.718.600, y redondear al millar inflaria el limite lo suficiente para que alguien
apenas por encima del tope apareciera como no obligado.

## El motor tributario

Convierte un `CasoTributario` (los hechos, cada uno con su fuente) en una `Liquidacion`
trazable del formulario 210. Es una funcion pura: el mismo caso con los mismos parametros y
las mismas elecciones da siempre la misma cifra, y cada cifra queda con el nodo que la
produjo, su formula y el articulo que la manda.

```python
from declaras.optimizador import optimizar
from declaras.parametros import cargar
from declaras.render import borrador_html, memoria_markdown

p = cargar(2025)  # parametros del anio gravable, desde ag2025.yaml
r = optimizar(caso, p)  # evalua las elecciones legales y elige la mejor
r.liquidacion.valor("IMPUESTO_NETO")  # la cifra
memoria_markdown(r.liquidacion, caso)  # el porque, renglon por renglon
borrador_html(r.liquidacion, caso)  # el borrador del 210 para que alguien lo firme
```

| Modulo | Que hace |
|---|---|
| `parametros/` | UVT, topes y tabla del art. 241 de cada anio gravable, en YAML validado (`ag2025.yaml`). La tabla se valida entera: tramos ascendentes, contiguos, el primero desde cero y solo el ultimo abierto |
| `caso/` | El `CasoTributario`: ingresos, beneficios, patrimonio y creditos, cada hecho con su `Fuente` |
| `motor/` | La liquidacion: base bruta, cedula general (art. 336), cedula de pensiones, dividendos, tabla del art. 241 y cierre (descuentos, anticipo, saldo) |
| `optimizador/` | Enumera las decisiones legales abiertas, liquida cada combinacion y se queda con la de menor impuesto, con desempate determinista |
| `render/` | El borrador del 210 en HTML y la memoria de calculo en Markdown, en el orden de las casillas |
| `dinero.py` | Unico punto de redondeo del sistema |

Tres reglas de fondo:

1. **El limite del art. 336 se aplica como manda la ley.** Las deducciones y rentas exentas
   de la cedula general se topan en `min(40% de los ingresos netos, 1.340 UVT)`, y el motor
   deja escrito en la traza el tope y lo que quedo por fuera. Los 72 UVT por dependiente van
   *fuera* del tope y el 10% del art. 387 va *dentro*: por eso no son reglas fijas sino
   elecciones que el optimizador enumera, porque cual conviene depende del caso.
2. **La tabla del art. 241 se evalua con la formula publicada**, no con una tabla de
   resultados: `(base − limite inferior del tramo) × tarifa + constante del tramo`, todo en
   UVT del anio. Cambiar de anio gravable es cambiar el YAML, no el codigo.
3. **Dinero en pesos enteros**, con un unico redondeo half-up en `dinero.pesos()`. Los
   productos pasan por `dinero.porcentaje()` con `Decimal`: multiplicar en float antes de
   redondear desviaba cifras con tarifas como 0,35.

### Correr sus pruebas

```bash
uv run pytest tests/golden -q          # los 6 casos completos
uv run pytest tests/unit/motor tests/unit/caso tests/unit/parametros \
              tests/unit/optimizador tests/unit/render -q
make test                              # toda la suite del repositorio
```

Los seis goldens son el candado del motor: casos completos de punta a punta (asalariado,
pensionado, rentas de capital con dividendos, no obligado, pension no uniforme con anticipo
por promedio) con el impuesto calculado a mano. Cualquier cambio que mueva una de esas
cifras necesita justificacion normativa; no se ajusta el golden a lo que salio.

## Pendiente antes de produccion

- [ ] **Calibrar los descargadores de documentos** contra una sesion autenticada. El
      login ya esta calibrado; las paginas internas requieren `make explorar`.
- [ ] Cifrado en reposo con KMS si alguna vez hay que persistir credenciales.
- [ ] Migraciones con Alembic cuando el esquema empiece a evolucionar con datos reales.
- [ ] Alertas sobre `DIAN_LAYOUT_CHANGED`: es la senal de que el portal cambio.
- [ ] Trazas distribuidas (OpenTelemetry) cuando entren mas servicios al monorepo.
