# ADR 0004: usar la API REST de la DIAN para lo que el portal esconde

Fecha: 2026-07-25. Estado: aceptado. Descubierto por inspeccion del portal real.

## Contexto

Las declaraciones de renta (la del anio anterior y el borrador del anio en curso) no estan
en el portal JSF. El item "Consultar documento Diligenciado" solo imprime un PDF si ya se
conoce el numero del documento, y "Diligenciar / Presentar" redirige a
`WebDilGestorFormularios`, una aplicacion Angular.

Al observar esa aplicacion aparecio una API REST completa en `api.dian.gov.co`, que es la
que consumen las aplicaciones modernas de la entidad.

## Como se autentica

Tres pasos, todos replicables sin navegador:

1. Login normal en el portal, que deja la cookie `DIAN-MUISCA`.
2. `POST /identidad/sts/v2/cookies/token` con el header
   `Authorization: Digest <base64 del valor de la cookie>`. Devuelve `idToken` (un JWT
   firmado por el portal), `accessToken`, `refreshToken` y `expireIn: 3600`.
3. Las llamadas van con `Authorization: Bearer <idToken>` **y el header `clientid`**. Ese
   ultimo es el detalle que no es obvio: sin el, la API responde 400 o 401 aunque el token
   sea valido. Tambien viajan `x-request-id` y `etag` con el mismo identificador.

## Endpoints confirmados

| Metodo y ruta | Que devuelve |
|---|---|
| `GET /identidad/sts/v1/tokens/userinfo` | Identidad completa: nombres, tipo de documento, unidad, si actua a nombre propio |
| `GET /documentos/renta210ingreso/v1/anios` | Anios gravables disponibles (2005 a 2025) |
| `GET /documentos/renta210ingreso/v1/formatos/210/versionesRuta` | Version vigente del 210 (18), su `uriApi` (`documentos/renta210v18/v1`) y su `urlFormato` |
| `GET /documentos/renta210ingreso/v1/formularios?estado=presentado` | **Declaraciones presentadas**, con id de documento, anio, fecha de presentacion y si admite correccion |
| `GET /documentos/renta210ingreso/v1/formularios?estado=pendiente` | **Borradores abiertos**, con id, anio y si son editables |
| `GET /documentos/renta210ingreso/v1/contribuyente/fechaInscripcionRut` | Fecha de inscripcion en el RUT |
| `GET /documentos/renta210ingreso/v1/formularios/{id}/firmantes/{id}/esFirmaElectron` | Si el contribuyente tiene instrumento de firma electronica |
| `GET /rut/v10/contribuyentes/{documento}/actividadeseconomicas` | Actividad economica del RUT |
| `GET /rut/v10/contribuyentes/{documento}/tipocontribuyente` | Tipo de contribuyente y si es gran contribuyente |
| `GET /gestorformularios/generales/v1/personas/{tipo}-{numero}/formatos?paraSelector=true` | Catalogo de los 41 formularios que puede presentar la persona |

## Decision

Se implementa `DianApiClient` como cliente de esa API, reusando la sesion del portal. La
API se usa para lo que el portal no expone de forma razonable; los documentos que el portal
si entrega (RUT, exogena, facturas) siguen bajandose por postback, que ya funciona.

## Por que importa mas alla de estos documentos

Tres cosas que cambian el plan del producto:

1. **El inventario de declaraciones se sabe de entrada.** Antes de pedirle un solo dato al
   cliente se puede saber si ya presento, de que anios, si tiene un borrador abierto y si
   su declaracion admite correccion. Eso alimenta directamente la conversacion del agente
   y el producto de anios atrasados.
2. **La firma electronica es consultable.** Se puede saber si el contribuyente ya tiene el
   instrumento antes de llegar al momento de firmar, en vez de descubrirlo al final.
3. **Es el camino del presentador.** El formulario 210 vive en `documentos/renta210v18/v1`
   segun su propio manifiesto de versiones. Si el diligenciamiento se hace por esa API en
   vez de manejando un navegador sobre una SPA, el presentador pasa de ser la pieza mas
   fragil del sistema a una integracion como cualquier otra.

## Pendiente

Falta la ruta que entrega el contenido o el PDF de una declaracion concreta: las rutas
evidentes bajo `renta210v18` responden 404, asi que hay que observar la aplicacion abriendo
un formulario real. Ese mismo trabajo revela la API de escritura, o sea el presentador.
