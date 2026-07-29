"""Los conceptos con que el conciliador normaliza los códigos oficiales de la exógena.

Cada fila de la exógena trae su código oficial ("Salarios (Concepto: 5001)") y la columna
"Uso declaración Sugerida", donde la propia DIAN dice a qué renglón del 210 y a qué tope va
el valor; el lector ya deja eso resuelto en la fila (`form_lines`, `thresholds`). Lo que el
cruce necesita además es una identidad estable por tercero: dos códigos que son el mismo
hecho económico tienen que caer en la misma partida, o el certificado del tercero nunca
empareja con lo reportado.
"""

from enum import StrEnum


class Concepto(StrEnum):
    """El hecho económico detrás de un código de la exógena o de un certificado."""

    SALARIOS = "SALARIOS"
    HONORARIOS = "HONORARIOS"
    SERVICIOS = "SERVICIOS"
    ARRENDAMIENTOS = "ARRENDAMIENTOS"
    RENDIMIENTOS = "RENDIMIENTOS"
    DIVIDENDOS = "DIVIDENDOS"
    PENSIONES = "PENSIONES"
    APORTES_SALUD = "APORTES_SALUD"
    APORTES_PENSION = "APORTES_PENSION"
    RETENCION = "RETENCION"
    OTROS = "OTROS"
    # Lo que la DIAN manda a R29 (patrimonio bruto) o R30 (deudas). NO es renta: es un saldo al
    # 31 de diciembre. Clasificarlo como ingreso declararia como renta la plata que uno tiene.
    PATRIMONIO = "PATRIMONIO"
    DEUDA = "DEUDA"
    # Filas que no van a NINGUN renglon del 210 y solo sirven para determinar si la persona esta
    # obligada a declarar (los consumos con tarjeta, los movimientos en cuentas). No son una
    # decision: su unica funcion ya la cumplen los cinco topes, que se calculan y se muestran
    # aparte. Pedirle a alguien que decida que hacer con "77 millones de movimientos en cuentas"
    # es pedirle una decision que no existe.
    SOLO_PARA_TOPE = "SOLO_PARA_TOPE"


# Tabla INCREMENTAL: solo los códigos verificados contra reportes reales de la exógena y el
# formato de reporte de terceros. Un código que no esté acá NO es un hueco que tapar con un
# default: `concepto_de_codigo` devuelve None, la partida nace CONCEPTO_DESCONOCIDO y la
# decisión es del contador. Clasificarlo de oficio (por ejemplo como OTROS) lo metería a un
# renglón del 210 sin que nadie lo haya mirado.
_CODIGO_A_CONCEPTO: dict[str, Concepto] = {
    "5001": Concepto.SALARIOS,
    "5002": Concepto.HONORARIOS,
    # 5003 son comisiones: para el cruce es el mismo hecho que los honorarios (mismo
    # tratamiento, mismo certificado del tercero), así que comparten partida.
    "5003": Concepto.HONORARIOS,
    "5004": Concepto.SERVICIOS,
    "5005": Concepto.ARRENDAMIENTOS,
    "5010": Concepto.RENDIMIENTOS,
    "5016": Concepto.OTROS,
    # ── verificados contra un reporte real, citando lo que la DIAN dice de cada uno ──
    #
    # "Pagos por salarios" → "Tope 1: Ingresos brutos | R32 Ingresos brutos por rentas de
    # trabajo (art. 103 E.T.)". El mismo renglón que el 5001. Sin este mapeo el salario del
    # contribuyente nace CONCEPTO_DESCONOCIDO y el impuesto sale en cero: medido, $63.925.000
    # de ingreso laboral invisibles.
    "2276": Concepto.SALARIOS,
    # "Activos Proveedores" / "Activos aportes parafiscales, salud, pensión y cesantías" /
    # "Activos laborales reales consolidados" → todos "Tope 2: Patrimonio | R29 Patrimonio
    # Bruto". Son saldos al 31 de diciembre, no renta del año.
    "2201": Concepto.PATRIMONIO,
    "2214": Concepto.PATRIMONIO,
    "2215": Concepto.PATRIMONIO,
    # "Cuentas por pagar de clientes" → "R30 Deudas". Resta del patrimonio.
    "1315": Concepto.DEUDA,
    # "Total consumos o gastos con tarjeta Crédito o Débito" → "Tope 3: Consumos TC", y ningún
    # renglón del 210. Solo sirve para saber si está obligado a declarar.
    "1023": Concepto.SOLO_PARA_TOPE,
}


# Los conceptos que el motor NO liquida todavía (no hay cédula de independientes en el
# caso). Vive acá y no en el mapeo porque lo comparten las dos capas que deciden con él:
# `resolver` (la salida LLEVAR_A_MANO es SOLO para estos), `autorresolver` (no les pone
# provisional: garantizaría que a_caso truene y escondería la partida de la cola) y
# `a_caso` (un hecho de estos conceptos revienta). Cuando el motor cubra independientes,
# sacar el concepto de acá enciende su mapeo y apaga la salida manual en el mismo commit.
CONCEPTOS_FUERA_DEL_MOTOR = frozenset({Concepto.HONORARIOS, Concepto.SERVICIOS, Concepto.OTROS})


def concepto_de_codigo(code: str) -> Concepto | None:
    """El concepto de un código oficial, o None si no está mapeado.

    None no es una falla: es la señal de que la partida va al contador como pregunta,
    nunca a una categoría por defecto.
    """
    return _CODIGO_A_CONCEPTO.get(code)
