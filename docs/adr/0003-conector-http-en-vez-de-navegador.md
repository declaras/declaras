# ADR 0003: el conector habla HTTP, no maneja un navegador

Fecha: 2026-07-25. Estado: aceptado. Verificado contra el portal real.

## Contexto

El Muisca no publica una API de datos y su portal es JSF, asi que el punto de partida fue
automatizar un navegador. La pregunta era si se podia reemplazar por llamadas HTTP
directas, y se resolvio midiendo (`scripts/inspeccionar_red.py`).

Hallazgos de la inspeccion:

1. **El login tiene un servicio propio.** La app Angular envia las credenciales a
   `POST /IdentidadRest_Acceso/api/sts/v1/auth/weblogin` como formulario, con la clave en
   base64, mas `clientId`, `redirectUri` e `ideRequest`. Ese ultimo sale de la URL a la
   que redirige la entrada clasica y es un JSON en base64. El servicio exige headers
   `Origin` y `Referer` apuntando a la app: sin ellos responde 500.
2. **La respuesta siembra las cookies legacy** (`JSESSIONID` y `DIAN-MUISCA`), que son las
   que entiende el portal JSF.
3. **Los documentos se piden por postback.** Pulsar un icono del dashboard equivale a
   reenviar el formulario `vistaDashboard:frmDashboard` con sus campos ocultos (16) mas
   las coordenadas del boton (`<id>.x`, `<id>.y`). No hace falta `ViewState`.
4. **Se replico de punta a punta:** con esas dos llamadas mas el postback, el portal
   entrega el RUT con `content-type: application/pdf` y su `content-disposition`.
5. **Los flujos con ventana modal tambien se replican.** La exogena exige tres envios
   (abrir el modal, registrar el anio en un envio propio, y disparar el enlace de
   descarga escribiendo su id en el campo oculto `_idcl`) y entrega un XLSX. El boton del
   modal aparenta necesitar Ajax4jsf, pero su JavaScript solo fija el anio y delega en ese
   enlace, asi que no hace falta hablar el protocolo AJAX.

## Decision

El conector por defecto es HTTP (`DECLARAS_DIAN_ADAPTER=http`). El adaptador de Playwright
se conserva para los flujos que dependan de JavaScript y como respaldo si el portal
endurece las validaciones.

## Razones

Medido en la misma extraccion (login mas descarga del RUT): **4 segundos por HTTP contra
21 con navegador**. Ademas desaparecen Chromium (93 MB de descarga y cientos de MB de RAM
por sesion), los timeouts de renderizado y la fragilidad de esperar que una SPA monte sus
componentes. El servicio pasa a caber en un contenedor pequeno y a poder correr donde no
hay navegador.

Sobre la fragilidad, que era la duda razonable: los nombres de campo JSF resultaron ser
estables y semanticos (`btnConsultarRUT`, `btnExogena`), y los campos ocultos se leen del
HTML en cada peticion, asi que si el portal agrega uno, viaja solo. Cuando algo si cambie,
falla de forma ruidosa: la respuesta deja de ser un PDF y se reporta
`DIAN_LAYOUT_CHANGED`, nunca un exito silencioso.

## Consecuencias

- La evidencia de auditoria pasa de captura de pantalla a HTML archivado del portal, que
  cumple el mismo proposito y ademas sirve para depurar cambios.
- La verificacion de identidad no esta cubierta por este camino: si el portal la exige, el
  login HTTP falla y hay que resolverlo con el adaptador de navegador.
- Detectar una clave incorrecta no se puede confiar al codigo de estado: el portal puede
  responder 200 y no dejar sesion. Por eso `open_session` valida pidiendo el dashboard y
  buscando un marcador que solo existe autenticado.
- Los dos adaptadores implementan el mismo puerto `DianConnector`, asi que alternarlos es
  cambiar una variable de entorno y ningun servicio se toca.
