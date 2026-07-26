# ADR 0001: el conector se expone como API REST, no como servidor MCP

Fecha: 2026-07-25. Estado: aceptado.

## Contexto

El conector DIAN lo va a llamar otro agente (el bot de WhatsApp). Habia dos formas de
exponerlo: un servidor MCP, que es el protocolo natural para darle herramientas a un
modelo, o una API REST convencional.

## Decision

Se expone como **API REST con FastAPI**. Si mas adelante conviene que un modelo lo
invoque directamente, se agrega un servidor MCP delgado que llame a la misma capa de
servicios, sin duplicar logica.

## Razones

1. **Determinismo.** Esto no es una herramienta de conversacion: es un proceso que
   maneja la clave de la DIAN de una persona y puede bloquearle la cuenta. El
   contrato tiene que ser explicito y versionado, no interpretado por un modelo.
2. **Operaciones largas.** Una extraccion tarda minutos y el portal se cae. Eso exige
   jobs, arriendos, reintentos e idempotencia (ver ADR 0002), que en HTTP son
   estandar y en MCP habria que inventar.
3. **Robustez y observabilidad.** Codigos de error estables, `X-Retryable`, timeouts,
   autenticacion por llave, health checks y logs estructurados son territorio conocido
   en HTTP.
4. **El agente no pierde nada.** Un agente llama HTTP sin problema. MCP aportaria
   descubrimiento automatico de herramientas, que aqui no hace falta porque el flujo
   lo decide nuestro codigo, no el modelo.
5. **Un solo nucleo.** La logica vive en `services/`, que no sabe de transporte. Poner
   MCP encima despues cuesta poco justamente por eso.

## Consecuencias

El agente integra por HTTP con `X-API-Key`. Si aparece un caso donde un modelo deba
usar el conector como herramienta, se agrega el wrapper MCP; nunca una segunda
implementacion.
