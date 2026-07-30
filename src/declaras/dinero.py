from decimal import ROUND_HALF_UP, Decimal


def pesos(x) -> int:
    """Redondea a peso entero con half-up (0.5 sube). Único punto de redondeo del sistema."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def porcentaje(monto: int, tarifa: float) -> int:
    """Producto monto × tarifa con Decimal exacto y cierre en pesos() half-up.

    Nunca multiplicar en float antes de redondear: tarifas como 0,35 no son exactas
    en binario, así que un producto que cae justo en ,50 aterriza en ...,4999 y
    half-up lo baja un peso (89.844.110 × 35% daba 31.445.438 en vez de .439).
    """
    return pesos(Decimal(monto) * Decimal(str(tarifa)))


def en_pesos(monto: int) -> str:
    """Una cifra de plata como la escribe una persona en Colombia: $1.234.567.

    VIVE ACÁ porque la usan tres sitios que no deberían importarse entre ellos: los mensajes de
    aviso del cruce (`mapeo`), los textos de las peticiones (`peticiones`) y lo que se le manda al
    cliente por WhatsApp. La primera versión estaba solo en `peticiones`, así que los avisos del
    cruce escribían la plata con la coma de miles del inglés: "4,240,000 pesos" en una pantalla
    colombiana, donde eso se lee como cuatro pesos con veinticuatro centésimas.
    """
    return f"${monto:,.0f}".replace(",", ".")
