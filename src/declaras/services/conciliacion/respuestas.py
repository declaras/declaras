"""Las respuestas del cliente a las preguntas básicas del expediente.

Los beneficios invisibles (prepagada, dependientes, AFC...) no aparecen en la exógena ni
en los certificados que ya llegaron: hay que PREGUNTAR. La respuesta se registra acá, y
un "no" vale tanto como un "sí" — `tiene=False` persiste y apaga la petición para
siempre; sin este registro el sistema le pregunta por prepagada al cliente en cada
consulta. Las peticiones derivadas (siguiente tarea del plan) leen esto: beneficio sin
respuesta → pregunta; `tiene=True` sin documento → petición del certificado.
"""

from datetime import datetime

from declaras.services.conciliacion.modelos import _Modelo


class Respuesta(_Modelo):
    """Lo que el cliente contestó a UNA pregunta, con quién y cuándo.

    `pregunta` es la clave estable de la pregunta (p. ej. "PREPAGADA"), no su texto.
    `detalle` guarda lo que acompañó la respuesta (valores, precisiones) sin esquema
    fijo: cada pregunta define el suyo y quien la derive en petición lo interpreta.
    """

    pregunta: str
    tiene: bool
    detalle: dict[str, object]
    quien: str
    cuando: datetime
