"""Calendario tributario: reglas de fechas que el sistema deduce por si mismo.

La renta se declara el anio siguiente al que cierra, asi que durante 2026 lo que se
declara es el anio gravable 2025. Es una regla del calendario, no un dato del
contribuyente: preguntarselo solo agrega una pregunta que el sistema puede responder y
una oportunidad de equivocarse.

Se permite pedir un anio explicito porque existe un caso real: quien no declaro anios
anteriores y se quiere poner al dia.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

COLOMBIA_TZ = ZoneInfo("America/Bogota")

# Primer anio gravable que el conector sabe consultar en el portal.
EARLIEST_SUPPORTED_YEAR = 2015


def today_in_colombia() -> date:
    """Fecha de hoy en la zona del contribuyente, no la del servidor."""
    return datetime.now(COLOMBIA_TZ).date()


def default_tax_year(today: date | None = None) -> int:
    """Anio gravable que corresponde declarar en la fecha dada."""
    reference = today or today_in_colombia()
    return reference.year - 1


def is_supported_tax_year(year: int, today: date | None = None) -> bool:
    """Un anio es consultable si ya cerro y no es anterior al soporte del conector."""
    return EARLIEST_SUPPORTED_YEAR <= year <= default_tax_year(today)
