# ADR 0009: los trabajos publican en qué van, y ninguna falla de la DIAN sale como error interno

Fecha: 2026-07-26. Estado: aceptado.

## Contexto

Dos cosas que pasaron en el mismo intento real, con un contribuyente distinto al de calibración.

La primera: la API de la DIAN respondió `404` al preguntar por su declaración presentada, porque
esa persona nunca ha declarado. Ese `404` salía como una excepción de la librería HTTP, que nadie
reconocía como una falla de la DIAN, así que la extracción entera se caía con `INTERNAL_ERROR` y
el texto crudo de httpx (con una URL de la DIAN adentro) llegaba hasta la pantalla.

La segunda: mientras la consulta corría, la pantalla decía "Consultando…" y nada más. Contra el
portal real eso son cerca de treinta segundos mirando algo quieto, sin saber si está funcionando,
si la clave fue aceptada o si se colgó. Cuando falló, lo único que quedó fue un código interno.

Las dos comparten la misma raíz: el sistema sabía lo que estaba pasando y no lo estaba diciendo.

## Decisión 1: toda respuesta de la API se traduce a una falla del dominio

`_raise_for_status` reemplaza a `response.raise_for_status()` y mapea cada código a un error que
el sistema ya sabe manejar: `404` a documento no disponible, `401` a sesión vencida, `429` a
limitación, `5xx` a portal caído, y cualquier otro `4xx` a una falla genérica de la DIAN.

La regla de fondo es que **un problema con la DIAN nunca puede reportarse como un error nuestro**,
porque manda a buscar la causa al lado equivocado. Hay un caso que lo fija para los nueve códigos
más comunes.

Un `404` en esa API casi nunca es un error: es la DIAN diciendo que no tiene ese documento. Con la
traducción, el bucle que ya toleraba fallas por documento vuelve a funcionar y la extracción trae
los otros cuatro en vez de caerse entera.

## Decisión 2: los trabajos publican su avance, paso a paso

`Job` lleva `progress`: una lista de pasos con nombre en lenguaje de la persona que está
esperando, cada uno en `PENDING`, `RUNNING`, `DONE`, `EMPTY` o `FAILED`.

Tres cosas que no son obvias y son las que hacen que sirva:

**El plan se publica al encolar, no al empezar a trabajar.** Los pasos se conocen desde que se
hace la petición, así que la pantalla los pinta completos y en gris desde el primer instante. Una
lista que va apareciendo sola no deja ver cuánto falta, que es la mitad de lo que quiere saber
quien espera.

**`EMPTY` no es `FAILED`.** Que la DIAN no tenga un documento le pasa a quien declara por primera
vez y no hay nada que arreglar; marcarlo en rojo asusta sin motivo. El paso queda en gris con la
razón al lado.

**El primer paso es entrar al portal.** Que se marque en verde a los tres segundos responde de una
la pregunta que más importa cuando algo va mal, que es si la clave estaba bien.

Publicar el avance también renueva el lease del worker, así que reemplaza al latido que había
antes: mientras el trabajo reporta que avanza, no hay razón para que otro lo reclame. Y nunca
tumba el job: si no se puede escribir el avance, el trabajo sigue.

## Lo que la interfaz hace con eso

Muestra los pasos y los va marcando. El texto de progreso ya no se inventa en el navegador
("Trayendo tus documentos…"), que era una cadena que no correspondía con lo que estaba pasando de
verdad; ahora refleja lo que el backend publica.

Los errores conocidos se explican por su código, no por el mensaje del backend: el código es lo
estable. Una clave rechazada dice "La clave del portal de la DIAN no es correcta" y cuántos
intentos quedan antes de que la DIAN bloquee la cuenta, que es lo que alguien necesita para
decidir si vuelve a intentar. El código interno solo aparece cuando no hay explicación escrita,
que es cuando de verdad sirve para reportar el problema.

## Efecto lateral: los mensajes de las fallas estaban escritos para nadie

Al revisar esto salió que los veinte mensajes por defecto de la taxonomía de errores estaban sin
tildes, redactados como comentarios del código. Son texto que aparece en la pantalla justo cuando
algo no funciona, que es cuando peor cae leer una nota de desarrollador. Se reescribieron todos, y
la comprobación que ya existía para los avisos de los lectores ahora cubre también las fallas.
