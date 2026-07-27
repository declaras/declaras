"""El conciliador: el cruce entre lo que la DIAN ya sabe y lo que el cliente demuestra.

El orden temporal define la API. Primero llega la exógena, completa y de una:
`abrir(exogena)` deja cada grupo (tercero, concepto) como una partida en su estado inicial.
Después van llegando los certificados del cliente, de a uno y con días de diferencia:
`incorporar(partidas, documento)` cruza cada uno contra lo que ya había. El resultado son
partidas con sus dos versiones y uno de cinco desenlaces; `autorresolver` cierra lo que no
necesita persona (y deja el preliminar), `resolver` registra las decisiones del contador,
`refrescar` reconcilia lo resuelto cuando llegan datos nuevos, y `a_caso` convierte las
partidas resueltas en el `CasoTributario` que el motor liquida.
"""

from declaras.services.conciliacion.conceptos import Concepto, concepto_de_codigo
from declaras.services.conciliacion.cruce import TIPO_A_CLAVE, abrir, incorporar
from declaras.services.conciliacion.mapeo import (
    DECISIONES_CON_HECHO,
    DIVIDENDOS_SIN_DESAGREGAR,
    PENSION_DISTRIBUIDA_UNIFORME,
    RETENCION_SIN_INGRESO,
    a_caso,
    avisos,
)
from declaras.services.conciliacion.modelos import (
    Decision,
    EstadoPartida,
    Lado,
    Motivo,
    Origen,
    Partida,
    Resolucion,
    Valor,
)
from declaras.services.conciliacion.resolucion import (
    NOTA_VALORES_CAMBIARON,
    QUIEN_SISTEMA,
    autorresolver,
    pendientes,
    refrescar,
    resolver,
)
from declaras.services.conciliacion.respuestas import Respuesta

__all__ = [
    "DECISIONES_CON_HECHO",
    "DIVIDENDOS_SIN_DESAGREGAR",
    "NOTA_VALORES_CAMBIARON",
    "PENSION_DISTRIBUIDA_UNIFORME",
    "QUIEN_SISTEMA",
    "RETENCION_SIN_INGRESO",
    "TIPO_A_CLAVE",
    "Concepto",
    "Decision",
    "EstadoPartida",
    "Lado",
    "Motivo",
    "Origen",
    "Partida",
    "Resolucion",
    "Respuesta",
    "Valor",
    "a_caso",
    "abrir",
    "autorresolver",
    "avisos",
    "concepto_de_codigo",
    "incorporar",
    "pendientes",
    "refrescar",
    "resolver",
]
