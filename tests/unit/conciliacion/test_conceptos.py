from declaras.services.conciliacion import Concepto, concepto_de_codigo


def test_codigos_conocidos():
    assert concepto_de_codigo("5001") is Concepto.SALARIOS
    assert concepto_de_codigo("5004") is Concepto.SERVICIOS


def test_codigo_desconocido_devuelve_none_no_un_default():
    assert concepto_de_codigo("9999") is None
    assert concepto_de_codigo("") is None


def test_dos_codigos_distintos_pueden_normalizar_al_mismo_concepto():
    """5002 (honorarios) y 5003 (comisiones) son el mismo hecho para el cruce."""
    assert concepto_de_codigo("5002") is Concepto.HONORARIOS
    assert concepto_de_codigo("5003") is Concepto.HONORARIOS


# ─────────────── el codigo del salario, y lo que no es una decision ───────────────
#
# Verificado contra un reporte real de exogena: la columna "Uso declaracion Sugerida" de la
# propia DIAN dice a que renglon del 210 va cada fila, y ese veredicto es la fuente.


def test_el_2276_no_se_mapea_por_codigo_porque_es_un_formato_y_no_un_concepto():
    """CORRIGE UN MAPEO QUE ESTABA MAL Y COSTABA PLATA.

    El 2276 es el FORMATO de rentas de trabajo y pensiones, y dentro trae siete conceptos
    distintos. Estaba mapeado entero a SALARIOS, así que el cruce sumaba las siete filas como
    sueldo. Medido en un reporte real de un solo empleador:

        $49.250.000  Pagos por salarios                         → ingreso
         $4.771.000  Prestaciones sociales                      → ingreso
           $179.000  Cesantías e intereses pagadas              → ingreso
         $2.285.000  Cesantías consignadas al fondo             → ingreso
         $1.970.000  Aporte obligatorio pensión                 → INCRNGO: RESTA
         $1.970.000  Aportes obligatorios salud                 → INCRNGO: RESTA
         $3.500.000  Valor ingreso laboral promedio 6 meses     → dato, no es plata

    Resultado: ingreso bruto de $63.925.000 donde el real era $56.485.000, y $3.940.000 de aportes
    sumando en vez de restar. La base quedaba inflada $11.380.000 y la casilla 33 en cero, que es
    justo la que la DIAN cruza contra lo que ella misma precargó.

    Las filas del 2276 las clasifica `_concepto_de_fila` por el renglón que la DIAN les asigna y por
    su texto, que es lo único que las distingue. `test_cruce` lo cubre fila por fila.
    """
    assert concepto_de_codigo("2276") is None


def test_los_codigos_de_activos_laborales_son_patrimonio_no_ingreso():
    """2201, 2214 y 2215 los manda la DIAN a "R29 Patrimonio Bruto", no a un renglon de
    ingreso. Clasificarlos como ingreso declararia como renta lo que es un saldo."""
    for codigo in ("2201", "2214", "2215"):
        assert concepto_de_codigo(codigo) is Concepto.PATRIMONIO, codigo


def test_las_cuentas_por_pagar_son_deuda():
    """El 1315 va a "R30 Deudas": resta del patrimonio, no suma a la renta."""
    assert concepto_de_codigo("1315") is Concepto.DEUDA


def test_los_consumos_con_tarjeta_no_van_a_ningun_renglon_del_210():
    """El 1023 solo cuenta para el "Tope 3: Consumos TC", que sirve para saber si la persona
    esta obligada a declarar. No es un ingreso, no es patrimonio y no se declara en ninguna
    casilla: pedirle a alguien que "decida" que hacer con el es pedirle una decision que no
    existe."""
    assert concepto_de_codigo("1023") is Concepto.SOLO_PARA_TOPE
