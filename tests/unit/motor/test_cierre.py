import pytest

from declaras.caso import (
    Activo,
    Arriendo,
    Beneficios,
    CasoTributario,
    Contribuyente,
    Creditos,
    Dependiente,
    Deuda,
    Donacion,
    Fuente,
    IngresoLaboral,
    IngresoPension,
    MontoDeclarado,
    Movimientos,
    Patrimonio,
    Rendimiento,
)
from declaras.motor import Elecciones, liquidar
from declaras.parametros import cargar

FX = Fuente.fixture("test")
P = cargar(2025)


def _caso_laboral(retencion=8_000_000, **creditos_kw):
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="1", nombre="X"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=120_000_000,
                aportes_salud=4_800_000,
                aportes_pension=4_800_000,
                retencion=retencion,
                fuente=FX,
            )
        ],
        creditos=Creditos(**creditos_kw),
    )


def _caso_pensionado(activos_31dic: int) -> CasoTributario:
    """Pensión 4M/mes (48M/año), 100% exenta (< 1.000 UVT/mes); RLG_PENSIONES = 0."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="7", nombre="P"),
        pensiones=[IngresoPension(pagador="Colpensiones", mesadas=[4_000_000] * 12, fuente=FX)],
        patrimonio=Patrimonio(
            activos=[
                Activo(tipo="cuenta", descripcion="ahorros", valor_31dic=activos_31dic, fuente=FX)
            ],
            patrimonio_liquido_anterior=100_000_000,
        ),
    )


def test_saldo_a_favor_primer_anio():
    liq = liquidar(
        _caso_laboral(anios_previos_declarando=0), P, Elecciones(usar_387=False, usar_72uvt=False)
    )
    # sin beneficios: 25% = min(25%×110.4M, 790 UVT) = 27.600.000 ≤ cap 44.16M
    # RLG = 110.4M − 27.6M = 82.800.000 → imp241 = 28.519.090×0.19 = 5.418.627
    assert liq.valor("IMPUESTO_NETO") == 5_418_627
    assert liq.valor("RETENCIONES") == 8_000_000
    # anticipo 25% × 5.418.627 = 1.354.657 − 8M → 0
    assert liq.valor("ANTICIPO_SIGUIENTE") == 0
    assert liq.valor("SALDO") == 5_418_627 - 8_000_000  # a favor


def test_anticipo_promedio_dos_anios():
    liq = liquidar(
        _caso_laboral(anios_previos_declarando=2, impuesto_neto_anio_anterior=1_000_000),
        P,
        Elecciones(usar_387=False, usar_72uvt=False),
    )
    imp = liq.valor("IMPUESTO_NETO")  # 5.418.627
    promedio = round((imp + 1_000_000) / 2)  # 3.209.314 (menor que imp)
    esperado = max(0, round(promedio * 0.75) - 8_000_000)
    assert liq.valor("ANTICIPO_SIGUIENTE") == esperado == 0


def test_obligado_por_patrimonio_y_comparacion():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="0", nombre="G0"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT", valor_31dic=250_000_000, fuente=FX)],
            deudas=[],
            patrimonio_liquido_anterior=200_000_000,
        ),
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 1  # patrimonio > 4.500 UVT
    assert liq.valor("IMPUESTO_NETO") == 0
    assert liq.tiene_flag("COMPARACION_PATRIMONIAL")  # creció 50M sin rentas


def test_anticipo_con_retenciones_bajas():
    liq = liquidar(
        _caso_laboral(retencion=1_000_000, anios_previos_declarando=2),
        P,
        Elecciones(usar_387=False, usar_72uvt=False),
    )
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


# --- El borde exacto de los topes de obligación (art. 592 / 594-3 ET) ---
#
# La norma es asimétrica a propósito y `cierre.py` la sigue: ingresos con `>=` (el art. 592
# num. 1 define al no obligado por ingresos "inferiores a 1.400 UVT", así que llegar al tope
# ya obliga) y patrimonio, consignaciones y compras con `>` (el mismo numeral dice patrimonio
# que "no exceda de 4.500 UVT", y el art. 594-3 usa verbo estricto en los de flujo). Sin
# estos casos se podía voltear cualquiera de los cuatro comparadores y la suite seguía verde;
# la misma regla vive también en `tax/obligation.py`, con su propio test de borde.
#
# El criterio de consumos con tarjeta de crédito NO se prueba acá porque `cierre.py` no lo
# evalúa: `Movimientos` no tiene el campo. Es un falso negativo conocido, con ticket aparte.


def _caso_con_patrimonio(valor_31dic: int) -> CasoTributario:
    """Solo patrimonio bruto: sin ingresos ni movimientos, ningún otro criterio interfiere."""
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="10", nombre="Borde"),
        patrimonio=Patrimonio(
            activos=[Activo(tipo="cuenta", descripcion="CDT", valor_31dic=valor_31dic, fuente=FX)]
        ),
    )


def _caso_con_movimientos(**montos: int) -> CasoTributario:
    return CasoTributario(
        contribuyente=Contribuyente(num_doc="11", nombre="Borde"),
        movimientos=Movimientos(
            **{campo: MontoDeclarado(valor=valor, fuente=FX) for campo, valor in montos.items()}
        ),
    )


def test_patrimonio_exactamente_en_el_tope_no_obliga():
    # "Patrimonio bruto que no exceda de 4.500 UVT": estar en 224.095.500 no lo excede.
    liq = liquidar(_caso_con_patrimonio(P.uvt_pesos(4_500)), P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 0
    assert liq.tiene_flag("NO_OBLIGADO")


def test_patrimonio_un_peso_por_encima_del_tope_obliga():
    liq = liquidar(_caso_con_patrimonio(P.uvt_pesos(4_500) + 1), P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 1
    assert "patrimonio" in liq.nodos["OBLIGADO_DECLARAR"].formula


def test_consignaciones_exactamente_en_el_tope_no_obligan():
    liq = liquidar(
        _caso_con_movimientos(consignaciones_totales=P.uvt_pesos(1_400)), P, Elecciones()
    )
    assert liq.valor("OBLIGADO_DECLARAR") == 0
    assert liq.tiene_flag("NO_OBLIGADO")


def test_consignaciones_un_peso_por_encima_del_tope_obligan():
    liq = liquidar(
        _caso_con_movimientos(consignaciones_totales=P.uvt_pesos(1_400) + 1), P, Elecciones()
    )
    assert liq.valor("OBLIGADO_DECLARAR") == 1
    assert "consignaciones" in liq.nodos["OBLIGADO_DECLARAR"].formula


def test_compras_y_consumos_exactamente_en_el_tope_no_obligan():
    liq = liquidar(_caso_con_movimientos(compras_y_consumos=P.uvt_pesos(1_400)), P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 0
    assert liq.tiene_flag("NO_OBLIGADO")


def test_compras_y_consumos_un_peso_por_encima_del_tope_obligan():
    liq = liquidar(
        _caso_con_movimientos(compras_y_consumos=P.uvt_pesos(1_400) + 1), P, Elecciones()
    )
    assert liq.valor("OBLIGADO_DECLARAR") == 1
    assert "compras y consumos" in liq.nodos["OBLIGADO_DECLARAR"].formula


def test_ingresos_exactamente_en_el_tope_si_obligan():
    # El otro lado de la asimetría: acá el comparador SÍ es `>=` y el borde obliga.
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="12", nombre="Borde"),
        rendimientos=[Rendimiento(entidad="Banco", valor=P.uvt_pesos(1_400), fuente=FX)],
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.valor("OBLIGADO_DECLARAR") == 1
    assert "ingresos" in liq.nodos["OBLIGADO_DECLARAR"].formula


# --- Guard de año: el caso y los parámetros deben ser del mismo año gravable ---


def test_guard_anio_caso_vs_parametros():
    caso = CasoTributario(anio_gravable=2024, contribuyente=Contribuyente(num_doc="1", nombre="X"))
    with pytest.raises(ValueError, match="2024"):
        liquidar(caso, P, Elecciones())


# --- Flags de validación (carries): advierten, nunca alteran cifras ---


def test_flag_no_residente():
    caso = CasoTributario(contribuyente=Contribuyente(num_doc="2", nombre="NR", residente=False))
    liq = liquidar(caso, P, Elecciones())
    assert liq.tiene_flag("NO_RESIDENTE")


def test_flag_aportes_exceden_bruto():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="3", nombre="A"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=1_000_000,
                aportes_salud=1_500_000,
                aportes_pension=500_000,
                fuente=FX,
            )
        ],
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.tiene_flag("APORTES_EXCEDEN_BRUTO")


def test_flag_retencion_excede_ingreso():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="4", nombre="R"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=10_000_000,
                aportes_salud=400_000,
                aportes_pension=400_000,
                retencion=12_000_000,
                fuente=FX,
            )
        ],
        arriendos=[
            Arriendo(inmueble="Apto 101", canon_total=6_000_000, retencion=7_000_000, fuente=FX)
        ],
    )
    liq = liquidar(caso, P, Elecciones())
    # un flag por cada fuente cuya retención supera su base (laboral y arriendo)
    assert len([f for f in liq.flags if f.codigo == "RETENCION_EXCEDE_INGRESO"]) == 2


def test_flag_tope_descuento_donaciones():
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="5", nombre="D"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=120_000_000,
                aportes_salud=4_800_000,
                aportes_pension=4_800_000,
                retencion=8_000_000,
                fuente=FX,
            )
        ],
        beneficios=Beneficios(
            donaciones_esal=[
                Donacion(entidad="ESAL", valor=10_000_000, certificada=True, fuente=FX)
            ]
        ),
    )
    liq = liquidar(caso, P, Elecciones(usar_387=False, usar_72uvt=False))
    # impuesto a cargo = 5.418.627 → tope 258 = 1.354.657; descuento = 25%×10M
    assert liq.valor("DESCUENTO_DONACIONES") == 2_500_000
    assert liq.tiene_flag("TOPE_DESCUENTO_DONACIONES")
    # v1: solo advierte; la cifra NO se recorta
    assert liq.valor("IMPUESTO_NETO") == 5_418_627 - 2_500_000


@pytest.mark.parametrize("nit_segundo, duplicado", [("900", True), ("901", False)])
def test_flag_empleador_duplicado(nit_segundo, duplicado):
    # Dos 220 del mismo NIT: el certificado re-emitido es otro archivo, así que la
    # deduplicación por sha256 del upload no lo ve y el ingreso se cuenta dos veces.
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="6", nombre="Dup"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=60_000_000,
                aportes_salud=2_400_000,
                aportes_pension=2_400_000,
                fuente=FX,
            ),
            IngresoLaboral(
                empleador_nit=nit_segundo,
                empleador_nombre="ACME (v2)",
                salarios=60_000_000,
                aportes_salud=2_400_000,
                aportes_pension=2_400_000,
                fuente=FX,
            ),
        ],
    )
    liq = liquidar(caso, P, Elecciones())
    mensajes = [f.mensaje for f in liq.flags if f.codigo == "EMPLEADOR_DUPLICADO"]
    assert (len(mensajes) == 1) is duplicado
    if duplicado:
        assert "900" in mensajes[0]  # dice de qué NIT se trata
    # Advierte, no corrige: los dos ingresos siguen sumando en la cédula general.
    assert liq.valor("ING_BRUTO_GENERAL") == 120_000_000


@pytest.mark.parametrize("meses, parcial", [(12, False), (11, True), (1, True)])
def test_flag_dependiente_parcial(meses, parcial):
    # El motor da los 72 UVT y el 387 completos: con un dependiente de medio año la
    # cifra está de más y nadie lo notaría sin el flag.
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="7", nombre="Dep"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=120_000_000,
                aportes_salud=4_800_000,
                aportes_pension=4_800_000,
                fuente=FX,
            )
        ],
        beneficios=Beneficios(
            dependientes=[Dependiente(tipo="hijo_menor", meses=meses, fuente=FX)]
        ),
    )
    liq = liquidar(caso, P, Elecciones(usar_387=False, usar_72uvt=True))
    assert liq.tiene_flag("DEPENDIENTE_PARCIAL") is parcial
    assert liq.valor("EXTRA_LIMITE") == P.uvt_pesos(72)  # sin prorratear, como advierte


@pytest.mark.parametrize(
    "confianza, avisa",
    [
        (0.9, False),
        (0.7, False),
        (0.69, True),
        (0.3, True),
        (None, False),
    ],
)
def test_flag_confianza_baja_persiste_en_la_liquidacion(confianza, avisa):
    # La advertencia del upload muere con la respuesta HTTP; el contador audita el
    # borrador y la memoria, que se arman desde estos flags. Borde igual que en el API:
    # 0.7 pasa, 0.69 avisa, sin confianza declarada no se inventa alarma.
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="8", nombre="Conf"),
        laborales=[
            IngresoLaboral(
                empleador_nit="900",
                empleador_nombre="ACME",
                salarios=120_000_000,
                aportes_salud=4_800_000,
                aportes_pension=4_800_000,
                fuente=Fuente.documento("220", "abc123def456", confianza=confianza),
            )
        ],
    )
    liq = liquidar(caso, P, Elecciones())
    assert liq.tiene_flag("CONFIANZA_BAJA") is avisa
    if avisa:
        mensaje = next(f.mensaje for f in liq.flags if f.codigo == "CONFIANZA_BAJA")
        assert "ACME" in mensaje and str(confianza) in mensaje  # qué ingreso y cuánta


def test_flag_confianza_baja_cubre_las_demas_fuentes_de_ingreso():
    # No es un chequeo de laborales: cualquier ingreso con proveniencia dudosa avisa.
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="9", nombre="Conf2"),
        rendimientos=[
            Rendimiento(
                entidad="Banco Y",
                valor=8_000_000,
                fuente=Fuente.documento("extracto", "d0", confianza=0.4),
            )
        ],
        arriendos=[Arriendo(inmueble="Apto 101", canon_total=6_000_000, fuente=FX)],
    )
    liq = liquidar(caso, P, Elecciones())
    mensajes = [f.mensaje for f in liq.flags if f.codigo == "CONFIANZA_BAJA"]
    assert len(mensajes) == 1  # solo el rendimiento; el arriendo es fixture sin confianza
    assert "Banco Y" in mensajes[0]


def test_caso_limpio_sin_flags_de_validacion():
    liq = liquidar(_caso_laboral(), P, Elecciones(usar_387=False, usar_72uvt=False))
    for codigo in (
        "NO_RESIDENTE",
        "APORTES_EXCEDEN_BRUTO",
        "RETENCION_EXCEDE_INGRESO",
        "TOPE_DESCUENTO_DONACIONES",
        "EMPLEADOR_DUPLICADO",
        "DEPENDIENTE_PARCIAL",
        "CONFIANZA_BAJA",
    ):
        assert not liq.tiene_flag(codigo)


def test_el_patrimonio_liquido_no_baja_de_cero():
    """Quien debe más de lo que tiene declara 0 en la casilla 31, no un negativo.

    Comprobado contra una declaración real presentada: casilla 29 en $1.880.000, casilla 30 en
    $139.228.000 y casilla 31 en 0. El lector del 210 ya aplicaba el piso al validar las
    identidades del formulario; el motor no, así que las dos mitades del sistema discrepaban sobre
    la misma regla y la que producía nuestras cifras era la que estaba mal.
    """
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="7", nombre="P"),
        pensiones=[IngresoPension(pagador="Colpensiones", mesadas=[4_000_000] * 12, fuente=FX)],
        patrimonio=Patrimonio(
            activos=[
                Activo(tipo="cuenta", descripcion="ahorros", valor_31dic=1_880_000, fuente=FX)
            ],
            deudas=[Deuda(acreedor="Banco", saldo_31dic=139_228_000, fuente=FX)],
        ),
    )

    liq = liquidar(caso, cargar(2025), Elecciones())

    assert liq.valor("PATRIMONIO_BRUTO") == 1_880_000
    assert liq.valor("PATRIMONIO_LIQUIDO") == 0
    # La memoria dice que hubo piso, o el cero se lee como "no tiene deudas" y es lo contrario.
    assert "piso en cero" in liq.nodos["PATRIMONIO_LIQUIDO"].formula


def test_con_mas_activos_que_deudas_el_liquido_es_la_resta():
    """El piso no puede tragarse el caso normal."""
    caso = CasoTributario(
        contribuyente=Contribuyente(num_doc="7", nombre="P"),
        pensiones=[IngresoPension(pagador="Colpensiones", mesadas=[4_000_000] * 12, fuente=FX)],
        patrimonio=Patrimonio(
            activos=[
                Activo(tipo="inmueble", descripcion="apto", valor_31dic=300_000_000, fuente=FX)
            ],
            deudas=[Deuda(acreedor="Banco", saldo_31dic=120_000_000, fuente=FX)],
        ),
    )

    liq = liquidar(caso, cargar(2025), Elecciones())

    assert liq.valor("PATRIMONIO_LIQUIDO") == 180_000_000
    assert "piso" not in liq.nodos["PATRIMONIO_LIQUIDO"].formula
