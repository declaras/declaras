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

from declaras.services.conciliacion.conceptos import (
    CONCEPTOS_FUERA_DEL_MOTOR,
    Concepto,
    concepto_de_codigo,
)
from declaras.services.conciliacion.cruce import TIPO_A_CLAVE, abrir, incorporar
from declaras.services.conciliacion.liquidaciones import (
    SEVERIDAD_BLOQUEANTE,
    LiquidacionVersionada,
    bloqueantes,
    ganancia,
    hay_bloqueante,
    liquidar_conciliado,
    liquidar_y_versionar,
)
from declaras.services.conciliacion.mapeo import (
    DECISIONES_CON_HECHO,
    DIVIDENDOS_SIN_DESAGREGAR,
    INGRESO_EXCLUIDO,
    INGRESO_LLEVADO_A_MANO,
    PENSION_DISTRIBUIDA_UNIFORME,
    POSIBLE_DOBLE_CONTEO,
    RETENCION_DESPLAZADA,
    RETENCION_SIN_INGRESO,
    a_caso,
    avisos,
    movimientos_de,
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
from declaras.services.conciliacion.peticiones import (
    MAXIMO_PETICIONES,
    UMBRAL_AHORRO,
    Peticion,
    costo_de_cerrar,
    derivar_peticiones,
    etiqueta_de_pregunta,
)
from declaras.services.conciliacion.resolucion import (
    CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO,
    NOTA_VALORES_CAMBIARON,
    QUIEN_SISTEMA,
    autorresolver,
    pendientes,
    refrescar,
    resolver,
)
from declaras.services.conciliacion.respuestas import Respuesta

__all__ = [
    "CONCEPTOS_CON_DOCUMENTO_AUTORITATIVO",
    "CONCEPTOS_FUERA_DEL_MOTOR",
    "DECISIONES_CON_HECHO",
    "DIVIDENDOS_SIN_DESAGREGAR",
    "INGRESO_EXCLUIDO",
    "INGRESO_LLEVADO_A_MANO",
    "MAXIMO_PETICIONES",
    "NOTA_VALORES_CAMBIARON",
    "PENSION_DISTRIBUIDA_UNIFORME",
    "POSIBLE_DOBLE_CONTEO",
    "QUIEN_SISTEMA",
    "RETENCION_DESPLAZADA",
    "RETENCION_SIN_INGRESO",
    "SEVERIDAD_BLOQUEANTE",
    "TIPO_A_CLAVE",
    "UMBRAL_AHORRO",
    "Concepto",
    "Decision",
    "EstadoPartida",
    "Lado",
    "LiquidacionVersionada",
    "Motivo",
    "Origen",
    "Partida",
    "Peticion",
    "Resolucion",
    "Respuesta",
    "Valor",
    "a_caso",
    "abrir",
    "autorresolver",
    "avisos",
    "bloqueantes",
    "concepto_de_codigo",
    "costo_de_cerrar",
    "derivar_peticiones",
    "etiqueta_de_pregunta",
    "ganancia",
    "hay_bloqueante",
    "incorporar",
    "liquidar_conciliado",
    "liquidar_y_versionar",
    "movimientos_de",
    "pendientes",
    "refrescar",
    "resolver",
]
