"""Fábricas de partidas para los tests de resolución y de mapeo.

Construyen vía el cruce REAL (`abrir`/`incorporar`), nunca a mano: `Partida` no valida
coherencia entre campos (su docstring lo advierte, medido en el cierre de T4), así que la
única garantía de no fabricar un estado que el cruce jamás produce es pasar por el mismo
código que los produce. La única excepción es `partida_pension`: ningún código de exógena
mapea todavía a PENSIONES, así que se construye a mano con la MISMA forma que `abrir` le
dará cuando el código se mapee (id `nit:CONCEPTO`, un solo lado, `SOLO_DIAN`).

Se reusan los helpers de `test_cruce.py` (`_exogena`, `_fila`, `_cert_220`) para que las
partidas de acá y las de los tests del cruce salgan del mismo molde.
"""

from declaras.services.conciliacion import (
    Concepto,
    EstadoPartida,
    Lado,
    Partida,
    Valor,
    abrir,
    incorporar,
)
from tests.unit.conciliacion.test_cruce import _cert_220, _exogena, _fila


def partida_coincide() -> Partida:
    """DIAN y 220 dicen lo mismo (85.000.000, retención 0): COINCIDE."""
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000))
    assert p.estado is EstadoPartida.COINCIDE
    return p


def partida_discrepancia(diferencia: int = 2_400_000) -> Partida:
    """La DIAN dice 85.000.000 + `diferencia`; el 220 dice 85.000.000.

    Con tolerancia 0 cualquier `diferencia` > 0 es DISCREPANCIA (la tolerancia por defecto
    del cruce se comería una de 100 pesos y la fábrica mentiría su nombre).
    """
    partidas = abrir(_exogena(_fila("900111222", "5001", 85_000_000 + diferencia)))
    [p] = incorporar(partidas, _cert_220("900111222", 85_000_000), tolerancia_pesos=0)
    assert p.estado is EstadoPartida.DISCREPANCIA
    return p


def partida_solo_dian() -> Partida:
    """Solo la exógena sostiene el hecho: falta el documento del cliente."""
    [p] = abrir(_exogena(_fila("901999888", "5001", 9_000_000)))
    assert p.estado is EstadoPartida.SOLO_DIAN
    return p


def partida_ajena() -> Partida:
    """La fila de la DIAN fue reportada a otra identificación: nunca aporta hecho sola."""
    [p] = abrir(_exogena(_fila("901999888", "5001", 9_000_000, reportado_a="99999")))
    assert p.reportado_a == "99999"
    return p


def partida_solo_documento() -> Partida:
    """Un certificado que la DIAN no corrobora (todavía)."""
    [p] = incorporar([], _cert_220("900111222", 85_000_000))
    assert p.estado is EstadoPartida.SOLO_DOCUMENTO
    return p


def partida_concepto_desconocido() -> Partida:
    """Un código que la tabla no mapea: pregunta al contador, no un default."""
    [p] = abrir(_exogena(_fila("900777333", "9999", 5_000_000)))
    assert p.estado is EstadoPartida.CONCEPTO_DESCONOCIDO
    return p


def fila_retencion(nit: str = "900111222", monto: int = 8_000_000) -> dict:
    """Una fila que la DIAN asigna al renglón 132 (retenciones), como en el reporte real."""
    fila = _fila(nit, "5004", monto)
    fila["concept"] = "Retencion en la fuente (Concepto: 5004)"
    fila["suggested_use"] = "R132 Retenciones año gravable a declarar"
    fila["form_lines"] = [132]
    del fila["retencion"]  # el lector real no emite esa clave
    return fila


def partida_retencion(nit: str = "890903938", monto: int = 560_000) -> Partida:
    """La partida RETENCION que `abrir` crea de una fila R132."""
    [p] = abrir(_exogena(fila_retencion(nit, monto)))
    assert p.concepto is Concepto.RETENCION
    return p


def partida_pension(total: int = 66_000_000) -> Partida:
    """Pensión reportada por la exógena. A MANO: ningún código mapea aún a PENSIONES;
    la forma es la que `abrir` producirá cuando se mapee (ver el docstring del módulo)."""
    return Partida(
        id="800224808:PENSIONES",
        nit_tercero="800224808",
        nombre_tercero="COLPENSIONES",
        concepto=Concepto.PENSIONES,
        codigos_crudos=["5099"],
        version_dian=Valor(
            monto=total, retencion=None, lado=Lado.DIAN,
            tercero="COLPENSIONES", celda="A20", confianza=1.0,
        ),
        estado=EstadoPartida.SOLO_DIAN,
    )
