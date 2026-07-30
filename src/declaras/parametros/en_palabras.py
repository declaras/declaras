"""Los pasos del cálculo dichos en español, no en lenguaje de contador.

POR QUÉ EXISTE ESTE ARCHIVO. El motor rotula sus nodos como los rotula un contador, y tiene que
seguir haciéndolo: "INCRNGO aportes obligatorios salud/pensión" es el nombre correcto, es el que va
en la memoria que se anexa y es el que un contador reconoce sin leer nada más. Pero el producto se
vende por WhatsApp a alguien que declara renta una vez al año y nunca oyó la palabra INCRNGO. A esa
persona el mismo paso hay que decírselo como "lo que aportaste a salud y pensión".

NO REEMPLAZA LA ETIQUETA TÉCNICA, LA ACOMPAÑA. Las dos viajan en el API y quien pinta decide cuál
mostrar según a quién le habla. Traducir destruyendo el original dejaría al contador sin el nombre
que necesita para defender la cifra.

QUÉ NO SE PERMITE AQUÍ:

  - Prometer. "Te ahorraste" no va donde el paso solo mide una base.
  - Redondear el significado. El 25% del artículo 206 tiene un tope de 790 UVT y esconderlo haría
    que alguien espere un descuento que no le van a dar.
  - Dejar un código sin nombre. `test_en_palabras` recorre `ORDEN_CASILLAS` y falla si el motor
    agrega un nodo que nadie tradujo, porque el modo de falla silencioso es exactamente el que este
    archivo viene a cerrar: el titular viendo jerga sin que nada avise.
"""

from __future__ import annotations

# El nombre de cada paso para quien no es contador. La clave es el código del nodo del motor.
EN_PALABRAS: dict[str, str] = {
    "OBLIGADO_DECLARAR": "¿Tienes que declarar?",
    "PATRIMONIO_BRUTO": "Todo lo que tienes, sumado",
    "PATRIMONIO_LIQUIDO": "Lo que tienes menos lo que debes",
    "ING_BRUTO_GENERAL": "Todo lo que te pagaron en el año",
    "INCR_APORTES": "Lo que aportaste a salud y pensión",
    "INCR_CI": "La parte de tus rendimientos que fue solo inflación",
    "INCR_TOTAL": "Total que la ley no cuenta como ingreso",
    "ING_NETOS_GENERAL": "Tus ingresos después de esos descuentos",
    "COSTOS_ARRIENDOS": "Gastos de los inmuebles que arriendas",
    # El tope no es un beneficio, es el techo de todos los beneficios juntos. Decirlo así evita la
    # confusión más costosa de la declaración: creer que las deducciones se suman sin límite.
    "CAP_40": "Máximo que la ley te deja descontar en total",
    "DEDUCCIONES_LIMITADAS": "Tus deducciones, hasta donde caben en ese máximo",
    "EXENTA_25": "El 25% de tu salario que no se grava",
    "EXENTA_CESANTIAS": "La parte de tus cesantías que no se grava",
    "APLICADO_40": "Lo que efectivamente se descontó",
    "EXTRA_LIMITE": "Beneficios que no cuentan contra ese máximo",
    "RLG_GENERAL": "Sobre esto se calcula tu impuesto",
    "RLG_PENSIONES": "Sobre esto se calcula el impuesto de tu pensión",
    "DIV_NO_GRAVADOS": "Dividendos que no pagan impuesto",
    "DIV_GRAVADOS": "Dividendos que sí pagan impuesto",
    "IMP_DIV_35": "Impuesto de tus dividendos",
    "BASE_TABLA_241": "Sobre cuánto se aplica la tarifa",
    "IMPUESTO_241": "Impuesto según la tabla de la ley",
    "DESCUENTO_254_1": "Descuento por tus dividendos",
    "DESCUENTO_DONACIONES": "Descuento por tus donaciones",
    "IMPUESTO_NETO": "Tu impuesto, ya con los descuentos",
    "RETENCIONES": "Lo que ya te retuvieron durante el año",
    "ANTICIPO_SIGUIENTE": "Anticipo para el año que viene",
    # Puede ser negativo: el mismo nodo dice "me toca pagar" y "me devuelven". El signo lo
    # interpreta quien pinta, así que el nombre no puede comprometerse con una de las dos.
    "SALDO": "Lo que queda: a pagar o a favor",
}


def en_palabras(codigo: str, etiqueta: str) -> str:
    """El nombre del paso para el titular, o la etiqueta técnica si nadie lo tradujo.

    El respaldo es la etiqueta del motor y no un texto genérico: si aparece un nodo nuevo, es mejor
    que el titular vea jerga correcta que un "paso del cálculo" que no dice nada. `test_en_palabras`
    hace fallar la construcción en ese caso, así que el respaldo no debería llegar a producción.
    """
    return EN_PALABRAS.get(codigo, etiqueta)
