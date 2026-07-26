import pytest

from declaras.caso import (
    Activo, Arriendo, Beneficios, CasoTributario, Contribuyente, Creditos,
    Donacion, Fuente, IngresoLaboral, IngresoPension, Patrimonio,
)
from declaras.motor import Elecciones, liquidar
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso_laboral(retencion=8_000_000, **creditos_kw):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="X"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
            aportes_salud=4_800_000, aportes_pension=4_800_000,
            retencion=retencion, fuente=FX)],
        creditos=Creditos(**creditos_kw),
    )


def _caso_pensionado(activos_31dic: int) -> CasoTributario:
    """Pensión 4M/mes (48M/año), 100% exenta (< 1.000 UVT/mes); RLG_PENSIONES = 0."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="7", nombre="P"),
        pensiones=[IngresoPension(pagador="Colpensiones",
                                  mesadas=[4_000_000] * 12, fuente=FX)],
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="ahorros",
                            valor_31dic=activos_31dic, fuente=FX)],
            patrimonio_liquido_anterior=100_000_000),
    )


def test_saldo_a_favor_primer_anio():
    liq = liquidar(_caso_laboral(anios_previos_declarando=0), P,
                   Elecciones(usar_387=False, usar_72uvt=False))
    # sin beneficios: 25% = min(25%×110.4M, 790 UVT) = 27.600.000 ≤ cap 44.16M
    # RLG = 110.4M − 27.6M = 82.800.000 → imp241 = 28.519.090×0.19 = 5.418.627
    assert liq.valor("IMPUESTO_NETO") == 5_418_627
    assert liq.valor("RETENCIONES") == 8_000_000
    # anticipo 25% × 5.418.627 = 1.354.657 − 8M → 0
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == 5_418_627 - 8_000_000  # a favor


def test_anticipo_promedio_dos_anios():
    liq = liquidar(_caso_laboral(anios_previos_declarando=2,
                                 impuesto_neto_anio_anterior=1_000_000), P,
                   Elecciones(usar_387=False, usar_72uvt=False))
    imp = liq.valor("IMPUESTO_NETO")           # 5.418.627
    promedio = round((imp + 1_000_000) / 2)    # 3.209.314 (menor que imp)
    esperado = max(0, round(promedio * 0.75) - 8_000_000)
    assert liq.valor("ANTICIPO_SIGUIENTE") == esperado == 0


def test_obligado_por_patrimonio_y_comparacion():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="0", nombre="G0"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT",
                            valor_31dic=250_000_000, fuente=FX)],
            deudas=[], patrimonio_liquido_anterior=200_000_000),
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 1        # patrimonio > 4.500 UVT
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.tiene_flag("COMPARACION_PATRIMONIAL")  # creció 50M sin rentas


def test_anticipo_con_retenciones_bajas():
    liq = liquidar(_caso_laboral(retencion=1_000_000, anios_previos_declarando=2), P,
                   Elecciones(usar_387=False, usar_72uvt=False))
    # 75% × 5.418.627 = 4.063.970,25 → half-up 4.063.970 − 1.000.000 = 3.063.970
    assert liq.valor("ANTICIPO_SIGUIENTE") == 3_063_970
    assert liq.valor("SALDO") == 5_418_627 + 3_063_970 - 1_000_000


def test_comparacion_patrimonial_pension_exenta_justifica():
    # Ahorra 30M con 48M de pensión exenta: el art. 236 cuenta las rentas exentas
    # como justificación del incremento → NO debe disparar el flag.
    liq = liquidar(_caso_pensionado(activos_31dic=130_000_000), P, Elecciones())
    assert liq.valor("RLG_PENSIONES") == 0
    assert not liq.tiene_flag("COMPARACION_PATRIMONIAL")


def test_comparacion_patrimonial_dispara_si_excede_todo_ingreso():
    # Incremento 60M > 48M de ingreso pensional total → sigue disparando.
    liq = liquidar(_caso_pensionado(activos_31dic=160_000_000), P, Elecciones())
    assert liq.tiene_flag("COMPARACION_PATRIMONIAL")


def test_no_obligado():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="9", nombre="Z"))
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 0
    assert liq.tiene_flag("NO_OBLIGADO")


# --- Guard de año: el caso y los parámetros deben ser del mismo año gravable ---

def test_guard_anio_caso_vs_parametros():
    caso = CasoTributario(anio_gravable=2024,
                          contribuyente=Contribuyente(num_doc="1", nombre="X"))
    with pytest.raises(ValueError, match="2024"):
        liquidar(caso, P, Elecciones())


# --- Flags de validación (carries): advierten, nunca alteran cifras ---

def test_flag_no_residente():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="2", nombre="NR", residente=False))
    liq = liquidar(caso, P, Elecciones())
    assert liq.tiene_flag("NO_RESIDENTE")


def test_flag_aportes_exceden_bruto():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="3", nombre="A"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=1_000_000,
            aportes_salud=1_500_000, aportes_pension=500_000, fuente=FX)],
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.tiene_flag("APORTES_EXCEDEN_BRUTO")


def test_flag_retencion_excede_ingreso():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="4", nombre="R"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=10_000_000,
            aportes_salud=400_000, aportes_pension=400_000,
            retencion=12_000_000, fuente=FX)],
        arriendos=[Arriendo(inmueble="Apto 101", canon_total=6_000_000,
                            retencion=7_000_000, fuente=FX)],
    )
    liq = liquidar(caso, P, Elecciones())
    # un flag por cada fuente cuya retención supera su base (laboral y arriendo)
    assert len([f for f in liq.flags if f.codigo == "RETENCION_EXCEDE_INGRESO"]) == 2


def test_flag_tope_descuento_donaciones():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="5", nombre="D"),
        laborales=[IngresoLaboral(
            empleador_nit="900", empleador_nombre="ACME", salarios=120_000_000,
            aportes_salud=4_800_000, aportes_pension=4_800_000,
            retencion=8_000_000, fuente=FX)],
        beneficios=Beneficios(donaciones_esal=[
            Donacion(entidad="ESAL", valor=10_000_000, certificada=True, fuente=FX)]),
    )
    liq = liquidar(caso, P, Elecciones(usar_387=False, usar_72uvt=False))
    # impuesto a cargo = 5.418.627 → tope 258 = 1.354.657; descuento = 25%×10M
    assert liq.valor("DESCUENTO_DONACIONES") == 2_500_000
    assert liq.tiene_flag("TOPE_DESCUENTO_DONACIONES")
    # v1: solo advierte; la cifra NO se recorta
    assert liq.valor("IMPUESTO_NETO") == 5_418_627 - 2_500_000


def test_caso_limpio_sin_flags_de_validacion():
    liq = liquidar(_caso_laboral(), P, Elecciones(usar_387=False, usar_72uvt=False))
    for codigo in ("NO_RESIDENTE", "APORTES_EXCEDEN_BRUTO",
                   "RETENCION_EXCEDE_INGRESO", "TOPE_DESCUENTO_DONACIONES"):
        assert not liq.tiene_flag(codigo)
