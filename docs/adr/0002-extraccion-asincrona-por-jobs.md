# ADR 0002: la extraccion es asincrona, basada en jobs

Fecha: 2026-07-25. Estado: aceptado.

## Contexto

Extraer del Muisca toma entre pocos minutos y bastante mas: son varias navegaciones,
descargas y un portal historicamente inestable, sobre todo en temporada de
vencimientos. Un endpoint sincronico obligaria al cliente a sostener una conexion
abierta durante todo eso.

## Decision

`POST /v1/extractions` **encola** y responde `202` con un `job_id`. El estado se
consulta con `GET /v1/extractions/{job_id}` o llega al `callback_url`. Un worker en
proceso toma los jobs con arriendo (lease) y reintenta lo que es reintentable.

## Razones

1. Una conexion HTTP de varios minutos se cae en cualquier proxy y no sobrevive un
   despliegue.
2. El estado del job es justo lo que el agente necesita para conversar: puede decirle
   al usuario "sigo trabajando" en vez de quedarse bloqueado.
3. Habilita el patron relevo: si el portal pide un codigo del correo, el job queda en
   `AWAITING_CHALLENGE` mientras el usuario responde por WhatsApp, y la sesion del
   navegador sigue viva.
4. Los arriendos con TTL permiten recuperar jobs de un worker que murio.

## Consecuencias y limite conocido

Como la clave nunca se persiste, vive en memoria del proceso; por lo tanto **la API y
el worker deben desplegarse como un solo proceso** mientras la boveda sea en memoria.
Lo mismo aplica a las sesiones parqueadas por un reto de identidad: hay afinidad de
worker.

Para escalar horizontalmente hay que sustituir `InMemoryCredentialVault` por una
boveda respaldada por KMS y usar enrutamiento con afinidad para los retos. Los puertos
del dominio ya estan definidos para que ese cambio no toque la logica.
