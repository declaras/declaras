from decimal import ROUND_HALF_UP, Decimal


def pesos(x) -> int:
    """Redondea a peso entero con half-up (0.5 sube). Único punto de redondeo del sistema."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
