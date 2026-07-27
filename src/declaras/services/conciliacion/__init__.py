"""El conciliador: el cruce entre lo que la DIAN ya sabe y lo que el cliente demuestra.

El orden temporal define la API. Primero llega la exógena, completa y de una:
`abrir(exogena)` deja cada grupo (tercero, concepto) como una partida en su estado inicial.
Después van llegando los certificados del cliente, de a uno y con días de diferencia:
`incorporar(partidas, documento)` cruza cada uno contra lo que ya había. El resultado son
partidas con sus dos versiones y uno de cinco desenlaces, listas para que el contador
resuelva lo que no cierre solo.
"""

from declaras.services.conciliacion.conceptos import Concepto, concepto_de_codigo

__all__ = [
    "Concepto",
    "concepto_de_codigo",
]
